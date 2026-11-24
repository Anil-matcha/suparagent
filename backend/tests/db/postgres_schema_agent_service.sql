CREATE TABLE agent_service_audit (
	id VARCHAR(36) NOT NULL,
	owner_id VARCHAR NOT NULL,
	agent_id BIGINT NOT NULL,
	agent_name VARCHAR(100) DEFAULT '' NOT NULL,
	connection_id VARCHAR(36) NOT NULL,
	connection_name VARCHAR(120) DEFAULT '' NOT NULL,
	provider VARCHAR(32) NOT NULL,
	tool_name VARCHAR(200) NOT NULL,
	resources JSON NOT NULL,
	outcome VARCHAR(20) NOT NULL,
	approved_by VARCHAR(100),
	thread_id BIGINT,
	turn_id BIGINT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_agent_service_audit_agent ON agent_service_audit (agent_id);

CREATE INDEX ix_agent_service_audit_owner_created ON agent_service_audit (owner_id, created_at);

CREATE TABLE agent_service_provider (
	connection_id VARCHAR(36) NOT NULL,
	provider VARCHAR(32) NOT NULL,
	resource_limit INTEGER DEFAULT '3' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (connection_id),
	FOREIGN KEY(connection_id) REFERENCES custom_mcp_connection (id) ON DELETE CASCADE
);

CREATE TABLE agent_service_resource (
	id VARCHAR(36) NOT NULL,
	owner_id VARCHAR NOT NULL,
	connection_id VARCHAR(36) NOT NULL,
	kind VARCHAR(8) NOT NULL,
	address VARCHAR(200) NOT NULL,
	label VARCHAR(100) NOT NULL,
	provider_resource_id VARCHAR(200),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_agent_service_resource_address UNIQUE (connection_id, kind, address),
	FOREIGN KEY(connection_id) REFERENCES custom_mcp_connection (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_service_resource_owner ON agent_service_resource (owner_id);

CREATE TABLE agent_service_assignment (
	agent_id BIGINT NOT NULL,
	resource_id VARCHAR(36) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (agent_id, resource_id),
	FOREIGN KEY(agent_id) REFERENCES agent (id) ON DELETE CASCADE,
	FOREIGN KEY(resource_id) REFERENCES agent_service_resource (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_service_assignment_resource ON agent_service_assignment (resource_id);
