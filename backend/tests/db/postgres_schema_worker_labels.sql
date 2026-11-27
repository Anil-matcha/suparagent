CREATE TABLE worker_label (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	owner_id VARCHAR NOT NULL,
	account_id VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	gmail_label_id VARCHAR NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_worker_label_account_name ON worker_label (account_id, name);

CREATE TABLE worker_label_action (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	owner_id VARCHAR NOT NULL,
	agent_id BIGINT NOT NULL,
	account_id VARCHAR NOT NULL,
	thread_id VARCHAR NOT NULL,
	label_name VARCHAR NOT NULL,
	gmail_label_id VARCHAR NOT NULL,
	turn_id BIGINT,
	run_id BIGINT,
	request_key VARCHAR,
	status VARCHAR NOT NULL,
	code VARCHAR,
	applied_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	undone_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT fk_worker_label_action_agent_id FOREIGN KEY(agent_id) REFERENCES agent (id) ON DELETE CASCADE
);

CREATE INDEX ix_worker_label_action_account_thread ON worker_label_action (account_id, thread_id);

CREATE INDEX ix_worker_label_action_agent_applied_at ON worker_label_action (agent_id, applied_at);
