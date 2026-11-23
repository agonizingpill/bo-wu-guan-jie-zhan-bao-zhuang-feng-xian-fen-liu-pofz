import hashlib
import logging
import time
from pathlib import Path

from psycopg import Error as DatabaseError
from psycopg.types.json import Jsonb
from redis.exceptions import RedisError

from .loans import change_status, lock_loan
from .schemas import LoanStatus
from .scoring import model_artifact, rescore, score
from .storage import QUEUE, RECHECK_QUEUE, audit, connection, initialize, queue

HEARTBEAT = Path('/tmp/museum-heartbeat')


def process(case_id):
    with connection() as db:
        reference = db.execute('SELECT loan_id FROM triage_case WHERE id=%s', (case_id,)).fetchone()
        if not reference:
            return
        loan_id = reference['loan_id']
        lock_loan(db, loan_id)
        case = db.execute('SELECT * FROM triage_case WHERE id=%s', (case_id,)).fetchone()
        if case['status'] != 'queued':
            return
        try:
            probability, risk, evidence = score(case['input_snapshot'])
            artifact = model_artifact()
            # 评分与当时的模型在同一事务落档；模型存不下来就整体回滚，先不出分。
            # 存档点回滚只撤销模型写入，外层事务仍能把案件记为失败。
            with db.transaction():
                db.execute('''INSERT INTO model_archive (model_version,artifact_sha256,artifact,parameters,recorded_by)
                              VALUES (%s,%s,%s,%s,'worker') ON CONFLICT DO NOTHING''',
                           (artifact['model_version'], artifact['artifact_sha256'],
                            artifact['blob'], Jsonb(artifact['parameters'])))
        except Exception as exc:
            logging.exception('Model failed to score case %s', case_id)
            db.execute("UPDATE triage_case SET status='failed',error=%s,attempts=attempts+1 WHERE id=%s",
                       ('模型计算或留档失败。请检查包装快照后重试。', case_id))
            change_status(db, loan_id, LoanStatus.PACKING)
            audit(db, loan_id, 'worker', 'triage_failed', case_id=case_id, error_type=type(exc).__name__)
            return
        db.execute('''UPDATE triage_case SET status='scored', probability=%s, risk=%s, evidence=%s, model_sha256=%s,
                       attempts=attempts+1,computed_at=now(),error=NULL WHERE id=%s''',
                   (probability, risk, Jsonb(evidence), artifact['artifact_sha256'], case_id))
        if risk == 'high':
            db.execute("INSERT INTO unpack_review (case_id,status) VALUES (%s,'awaiting_witness')", (case_id,))
        change_status(db, loan_id, LoanStatus.REVIEW_REQUIRED if risk == 'high' else LoanStatus.READY)
        audit(db, loan_id, 'worker', 'triage_scored', case_id=case_id, risk=risk, probability=probability)
        print(f'case={case_id} loan={loan_id} risk={risk} probability={probability:.6f}', flush=True)


RECHECK_SQL = '''SELECT r.id, r.status, t.id AS case_id, t.loan_id, t.input_snapshot,
        t.probability AS sealed_probability, t.risk AS sealed_risk, t.model_sha256, t.evidence,
        (SELECT status FROM unpack_review WHERE case_id=t.id) AS review_status
   FROM triage_recheck r JOIN triage_case t ON t.id=r.case_id WHERE r.id=%s'''


def process_recheck(recheck_id):
    with connection() as db:
        row = db.execute(RECHECK_SQL, (recheck_id,)).fetchone()
        if not row or row['status'] != 'queued':
            return
        lock_loan(db, row['loan_id'])
        artifact = None
        if row['model_sha256']:
            artifact = db.execute('SELECT artifact FROM model_archive WHERE artifact_sha256=%s',
                                  (row['model_sha256'],)).fetchone()
        if not artifact:
            # 老档没有原版模型，结论为无法核对。
            db.execute("UPDATE triage_recheck SET status='unverifiable',computed_at=now(),error=NULL WHERE id=%s",
                       (recheck_id,))
            audit(db, row['loan_id'], 'worker', 'recheck_unverifiable',
                  recheck_id=recheck_id, case_id=row['case_id'])
            return
        try:
            blob = bytes(artifact['artifact'])
            if hashlib.sha256(blob).hexdigest() != row['model_sha256']:
                raise ValueError('留档模型指纹校验不符')
            probability, risk = rescore(blob, row['input_snapshot'], row['evidence'])
        except Exception as exc:
            logging.exception('Model failed to recheck %s', recheck_id)
            db.execute("UPDATE triage_recheck SET status='failed',error=%s WHERE id=%s",
                       ('复算计算失败，可重试。', recheck_id))
            audit(db, row['loan_id'], 'worker', 'recheck_failed', recheck_id=recheck_id,
                  case_id=row['case_id'], error_type=type(exc).__name__)
            return
        delta = probability - row['sealed_probability']
        changed = risk != row['sealed_risk']
        db.execute('''UPDATE triage_recheck SET status='done',probability=%s,risk=%s,probability_delta=%s,
                      risk_changed=%s,review_status=%s,model_sha256=%s,computed_at=now(),error=NULL WHERE id=%s''',
                   (probability, risk, delta, changed, row['review_status'], row['model_sha256'], recheck_id))
        audit(db, row['loan_id'], 'worker', 'recheck_completed', recheck_id=recheck_id, case_id=row['case_id'],
              probability=probability, probability_delta=delta, risk=risk, risk_changed=changed)
        print(f'recheck={recheck_id} case={row["case_id"]} loan={row["loan_id"]} '
              f'risk={risk} delta={delta:.6f}', flush=True)


def recover_queue(redis):
    # PostgreSQL keeps the pending intent if a producer or worker exits before completion.
    with connection() as db:
        pending = db.execute("SELECT id FROM triage_case WHERE status='queued' ORDER BY id LIMIT 100").fetchall()
        rechecks = db.execute("SELECT id FROM triage_recheck WHERE status='queued' ORDER BY id LIMIT 100").fetchall()
    if pending:
        redis.lpush(QUEUE, *(row['id'] for row in pending))
    if rechecks:
        redis.lpush(RECHECK_QUEUE, *(row['id'] for row in rechecks))


def main():
    initialize()
    with queue() as redis:
        while True:
            try:
                message = redis.brpop([QUEUE, RECHECK_QUEUE], timeout=2)
                if message:
                    key, task_id = message[0].decode(), int(message[1])
                    process_recheck(task_id) if key == RECHECK_QUEUE else process(task_id)
                else:
                    recover_queue(redis)
                HEARTBEAT.touch()
            except (DatabaseError, RedisError, ValueError):
                logging.exception('Queue operation failed')
                time.sleep(2)


if __name__ == '__main__':
    main()
