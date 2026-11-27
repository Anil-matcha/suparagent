CREATE TABLE shadow_prediction (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	owner_id VARCHAR NOT NULL,
	agent_id BIGINT NOT NULL,
	account_id VARCHAR NOT NULL,
	thread_id VARCHAR NOT NULL,
	rank INTEGER NOT NULL,
	category VARCHAR NOT NULL,
	owner_only BOOLEAN DEFAULT false NOT NULL,
	draft_ref VARCHAR,
	draft_digest VARCHAR,
	draft_text TEXT,
	turn_id BIGINT,
	predicted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT fk_shadow_prediction_agent_id FOREIGN KEY(agent_id) REFERENCES agent (id) ON DELETE CASCADE
);

CREATE INDEX ix_shadow_prediction_agent_predicted_at ON shadow_prediction (agent_id, predicted_at);

CREATE UNIQUE INDEX uq_shadow_prediction_mark ON shadow_prediction (agent_id, account_id, thread_id, turn_id);
