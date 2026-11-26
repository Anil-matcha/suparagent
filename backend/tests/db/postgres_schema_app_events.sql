CREATE TABLE app_event_trigger (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	owner_id VARCHAR NOT NULL,
	routine_id BIGINT NOT NULL,
	composio_trigger_id VARCHAR NOT NULL,
	composio_account_id VARCHAR NOT NULL,
	slug VARCHAR NOT NULL,
	query VARCHAR DEFAULT '' NOT NULL,
	polling_interval INTEGER,
	state VARCHAR DEFAULT 'active' NOT NULL,
	last_event_at TIMESTAMP WITH TIME ZONE,
	last_error VARCHAR,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT fk_app_event_trigger_routine_id FOREIGN KEY(routine_id) REFERENCES routine (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_app_event_trigger_composio_id ON app_event_trigger (composio_trigger_id);

CREATE UNIQUE INDEX uq_app_event_trigger_routine ON app_event_trigger (routine_id);

CREATE TABLE app_event_day (
	trigger_id BIGINT NOT NULL,
	day DATE NOT NULL,
	received INTEGER DEFAULT '0' NOT NULL,
	started INTEGER DEFAULT '0' NOT NULL,
	merged INTEGER DEFAULT '0' NOT NULL,
	duplicate INTEGER DEFAULT '0' NOT NULL,
	outside_share INTEGER DEFAULT '0' NOT NULL,
	rejected INTEGER DEFAULT '0' NOT NULL,
	unrouted INTEGER DEFAULT '0' NOT NULL,
	dropped_cap INTEGER DEFAULT '0' NOT NULL,
	dropped_quiet INTEGER DEFAULT '0' NOT NULL,
	PRIMARY KEY (trigger_id, day),
	CONSTRAINT fk_app_event_day_trigger_id FOREIGN KEY(trigger_id) REFERENCES app_event_trigger (id) ON DELETE CASCADE
);

CREATE TABLE app_event_receipt (
	trigger_id BIGINT NOT NULL,
	event_hash VARCHAR NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (trigger_id, event_hash),
	CONSTRAINT fk_app_event_receipt_trigger_id FOREIGN KEY(trigger_id) REFERENCES app_event_trigger (id) ON DELETE CASCADE
);

CREATE INDEX ix_app_event_receipt_received_at ON app_event_receipt (received_at);
