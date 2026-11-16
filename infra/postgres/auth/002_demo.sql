BEGIN;
INSERT INTO roles (id, code, name) VALUES ('f91ca18e-fd46-53d3-bf50-010299a37a3a', 'ADMIN', 'ADMIN') ON CONFLICT DO NOTHING;
INSERT INTO users (id, role_id, login, password_hash, is_active, created_at, disabled_at) VALUES ('b4703b75-0c9e-55cf-8ad2-bd607b00f7ed', 'f91ca18e-fd46-53d3-bf50-010299a37a3a', 'admin', 'pbkdf2_sha256$600000$a639910dee000ce19bd9a7c712902c6e$acb2c8c6149ceba708c919aad0d45578fea4f538782b3dc64fddbbb2dce1af45', true, '2026-09-30T11:00:00Z', NULL) ON CONFLICT DO NOTHING;
INSERT INTO roles (id, code, name) VALUES ('ae7a1f33-c33d-5092-b1ed-c54e62d1e44e', 'ANALYST', 'ANALYST') ON CONFLICT DO NOTHING;
INSERT INTO users (id, role_id, login, password_hash, is_active, created_at, disabled_at) VALUES ('c7fba0b2-b5bd-59e7-b722-4f0e6c5b4c45', 'ae7a1f33-c33d-5092-b1ed-c54e62d1e44e', 'analyst', 'pbkdf2_sha256$600000$52b5bb28010f647edd7f29f7bb342d08$49e242f842d40a0a91a57a01fcaf7cc75d6e4685fd18a9dc3711bc6491fbc9b1', true, '2026-09-30T11:00:00Z', NULL) ON CONFLICT DO NOTHING;
INSERT INTO roles (id, code, name) VALUES ('d9d4e3da-4478-5b89-8b3d-97b55f2656cc', 'MANAGER', 'MANAGER') ON CONFLICT DO NOTHING;
INSERT INTO users (id, role_id, login, password_hash, is_active, created_at, disabled_at) VALUES ('43817961-b437-5313-9754-19535c80b1b9', 'd9d4e3da-4478-5b89-8b3d-97b55f2656cc', 'manager', 'pbkdf2_sha256$600000$f7ab808bf5efad05ab2c74f122add834$b06110ff7b20431205c7d95021e630564b8fe19c79bf357999230da2615b7ff2', true, '2026-09-30T11:00:00Z', NULL) ON CONFLICT DO NOTHING;
COMMIT;
