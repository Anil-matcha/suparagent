CREATE TABLE muapi_tool_grant (
	agent_id BIGINT NOT NULL,
	tool_name VARCHAR(200) NOT NULL,
	schema_digest VARCHAR(64) NOT NULL,
	description TEXT NOT NULL,
	input_schema JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (agent_id, tool_name),
	FOREIGN KEY(agent_id) REFERENCES agent (id) ON DELETE CASCADE
);

CREATE INDEX ix_muapi_tool_grant_agent ON muapi_tool_grant (agent_id);
