CREATE TABLE shadow_feedback (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	owner_id VARCHAR NOT NULL,
	agent_id BIGINT NOT NULL,
	account_id VARCHAR NOT NULL,
	thread_id VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	reason VARCHAR NOT NULL,
	note VARCHAR,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT fk_shadow_feedback_agent_id FOREIGN KEY(agent_id) REFERENCES agent (id) ON DELETE CASCADE
);

CREATE INDEX ix_shadow_feedback_agent_created_at ON shadow_feedback (agent_id, created_at);

CREATE UNIQUE INDEX uq_shadow_feedback_target ON shadow_feedback (agent_id, account_id, thread_id, kind);
