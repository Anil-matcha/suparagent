CREATE TABLE saved_app (
	id VARCHAR NOT NULL,
	owner_id VARCHAR NOT NULL,
	agent_id BIGINT,
	thread_id BIGINT,
	page_id VARCHAR NOT NULL,
	name VARCHAR NOT NULL,
	pinned_rev INTEGER,
	versions JSON NOT NULL,
	uses JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_saved_app_owner_page ON saved_app (owner_id, page_id);
