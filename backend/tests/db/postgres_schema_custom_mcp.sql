CREATE TABLE custom_mcp_connection (
	id VARCHAR(36) NOT NULL,
	owner_id VARCHAR NOT NULL,
	name VARCHAR(100) NOT NULL,
	transport VARCHAR(32) DEFAULT 'streamable_http' NOT NULL,
	endpoint VARCHAR(300) NOT NULL,
	auth_mode VARCHAR(16) DEFAULT 'none' NOT NULL,
	auth_header_name VARCHAR(64),
	enabled BOOLEAN DEFAULT '0' NOT NULL,
	revision INTEGER DEFAULT '1' NOT NULL,
	tested_revision INTEGER,
	last_error_code VARCHAR(64),
	tools_snapshot JSON,
	tools_digest VARCHAR(64),
	last_tested_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_custom_mcp_connection_owner ON custom_mcp_connection (owner_id);

CREATE TABLE custom_mcp_tool_grant (
	agent_id BIGINT NOT NULL,
	connection_id VARCHAR(36) NOT NULL,
	tool_name VARCHAR(200) NOT NULL,
	schema_digest VARCHAR(64) NOT NULL,
	description TEXT DEFAULT '' NOT NULL,
	input_schema JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (agent_id, connection_id, tool_name),
	FOREIGN KEY(agent_id) REFERENCES agent (id) ON DELETE CASCADE,
	FOREIGN KEY(connection_id) REFERENCES custom_mcp_connection (id) ON DELETE CASCADE
);

CREATE INDEX ix_custom_mcp_tool_connection ON custom_mcp_tool_grant (connection_id);
