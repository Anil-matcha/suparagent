CREATE TABLE spend_ledger (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	owner_id VARCHAR NOT NULL,
	agent_id BIGINT NOT NULL,
	run_key VARCHAR NOT NULL,
	routine_run_id BIGINT,
	tool VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	cap_cents INTEGER NOT NULL,
	settled_cents INTEGER,
	reason VARCHAR,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	settled_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id)
);

CREATE INDEX ix_spend_ledger_agent_created ON spend_ledger (owner_id, agent_id, created_at);

CREATE INDEX ix_spend_ledger_run_key ON spend_ledger (run_key);
