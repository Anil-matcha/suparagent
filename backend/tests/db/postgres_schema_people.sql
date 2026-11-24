CREATE TABLE people_audit (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	action VARCHAR NOT NULL, 
	actor_person_id BIGINT, 
	actor_label VARCHAR DEFAULT '' NOT NULL, 
	subject_person_id BIGINT, 
	subject_label VARCHAR DEFAULT '' NOT NULL, 
	detail VARCHAR DEFAULT '' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_people_audit_created_at ON people_audit (created_at);

CREATE TABLE person (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	email VARCHAR, 
	display_name VARCHAR NOT NULL, 
	password_hash VARCHAR, 
	role VARCHAR NOT NULL, 
	permissions VARCHAR, 
	external_id VARCHAR, 
	status VARCHAR DEFAULT 'active' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	removed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_person_email ON person (email);

CREATE UNIQUE INDEX uq_person_external_id ON person (external_id);

CREATE TABLE person_invite (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	token_hash VARCHAR(64) NOT NULL, 
	email VARCHAR NOT NULL, 
	role VARCHAR NOT NULL, 
	permissions VARCHAR, 
	invited_by_person_id BIGINT, 
	invited_by_label VARCHAR DEFAULT '' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_person_invite_email ON person_invite (email);

CREATE UNIQUE INDEX uq_person_invite_token_hash ON person_invite (token_hash);
