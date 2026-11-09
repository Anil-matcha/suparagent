CREATE TABLE agent_revision (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	agent_id BIGINT NOT NULL,
	number BIGINT NOT NULL,
	source VARCHAR(16) NOT NULL,
	actor VARCHAR NOT NULL,
	changed_fields JSON NOT NULL,
	redacted JSON NOT NULL,
	restored_from BIGINT,
	snapshot JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(agent_id) REFERENCES agent (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_agent_revision_number ON agent_revision (agent_id, number);
