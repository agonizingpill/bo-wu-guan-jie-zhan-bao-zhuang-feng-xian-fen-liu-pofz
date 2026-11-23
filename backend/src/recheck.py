import logging

from fastapi import HTTPException
from redis.exceptions import RedisError

from .loans import dossier, lock_loan
from .storage import RECHECK_QUEUE, audit, queue


def enqueue(db, loan_id, actor):
    # 复算面向已封存的派车档案，只读原档，不解除封存。
    lock_loan(db, loan_id)
    case = db.execute('SELECT id,status FROM triage_case WHERE loan_id=%s', (loan_id,)).fetchone()
    if not case or case['status'] != 'scored':
        raise HTTPException(409, '风险分尚未归档，无法复算')
    existing = db.execute('SELECT id,status FROM triage_recheck WHERE case_id=%s', (case['id'],)).fetchone()
    if existing and existing['status'] == 'queued':
        raise HTTPException(409, '复算已在队列中，请稍候查看')
    if existing and existing['status'] == 'done':
        raise HTTPException(409, '复算结论已归档，可在本页查看差异')
    if existing:
        recheck_id = existing['id']
        db.execute("UPDATE triage_recheck SET status='queued',error=NULL,requested_by=%s,created_at=now() WHERE id=%s",
                   (actor, recheck_id))
    else:
        recheck_id = db.execute('''INSERT INTO triage_recheck (case_id,status,requested_by)
                                VALUES (%s,'queued',%s) RETURNING id''', (case['id'], actor)).fetchone()['id']
    audit(db, loan_id, actor, 'recheck_retried' if existing else 'recheck_queued',
          case_id=case['id'], recheck_id=recheck_id)
    return dossier(db, loan_id), recheck_id


def notify_worker(recheck_id):
    try:
        with queue() as redis:
            redis.lpush(RECHECK_QUEUE, recheck_id)
    except RedisError:
        logging.exception('Redis enqueue failed; worker will recover the committed recheck')
