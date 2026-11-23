-- 理赔复算：评分模型留档与复算结论。
-- model_archive 按内容指纹保存评分当时使用的模型，同名模型换参数后仍能取回原版。
CREATE TABLE IF NOT EXISTS model_archive (
    model_version text NOT NULL,
    artifact_sha256 text NOT NULL,
    artifact bytea NOT NULL,
    parameters jsonb NOT NULL DEFAULT '{}',
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model_version, artifact_sha256)
);
-- 新评分记录所用模型指纹；老档该列为空，复算时显示无法核对。
ALTER TABLE triage_case ADD COLUMN IF NOT EXISTS model_sha256 text;
-- 每笔分流案件最多一条复算结论，重试复用同一行，不重复记结论。
CREATE TABLE IF NOT EXISTS triage_recheck (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id bigint NOT NULL UNIQUE REFERENCES triage_case,
    status text NOT NULL CHECK (status IN ('queued', 'done', 'unverifiable', 'failed')),
    probability double precision CHECK (probability BETWEEN 0 AND 1),
    risk text CHECK (risk IN ('low', 'high')),
    probability_delta double precision,
    risk_changed boolean,
    review_status text,
    model_sha256 text,
    error text,
    requested_by text NOT NULL REFERENCES staff,
    created_at timestamptz NOT NULL DEFAULT now(),
    computed_at timestamptz
);
