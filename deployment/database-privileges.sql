-- psql variables app_role and app_database MUST be supplied explicitly.
-- Run as the administrator of a newly created, dedicated application database.
-- The login role itself is created by the deployment bootstrap, never by Java.
REVOKE ALL ON DATABASE :"app_database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"app_database" TO :"app_role";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"app_role";
-- Flyway needs DDL in this schema. This is a schema owner, NOT a cluster admin.
