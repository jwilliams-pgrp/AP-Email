-- Core local seed data.
-- Idempotent by design: safe to rerun during local development.

insert into routing_destinations (
  destination_code,
  display_name,
  email_address,
  parent_folder,
  label,
  send_teams_message,
  send_email
)
values
  ('MEDIUS_PROPERTIES', 'Medius Properties', 'medius.prop@hillwood.com', 'MEDIUS_PROPERTIES', null, false, true),
  ('TIFFANY_BECK', 'Tiffany Beck', 'Tiffany.Beck@hillwood.com', 'TIFFANY_BECK', null, false, true),
  ('MICHELE_FELLERS', 'Michele Fellers', 'Michelle.Fellers@hillwood.com;Aliyah.Reyes@hillwood.com', 'MICHELE_FELLERS', null, false, true),
  ('PATTIE_MCCLEAN', 'Pattie McClean', 'Pattie.McClean@hillwood.com', 'PATTIE_MCCLEAN', null, false, true),
  ('MEDIUS_MF', 'Medius Multifamily Queue', 'Medius.MF@hillwood.com', 'MEDIUS_MF', null, false, true),
  ('FOLDER_STATEMENTS', 'Escalate Statement', null, 'FOLDER_STATEMENTS', null, false, false),
  ('FOLDER_ACH', 'ACH', null, 'FOLDER_ACH', null, false, false),
  ('FOLDER_BEN_E_KEITH', 'Ben E Keith', null, 'FOLDER_BEN_E_KEITH', null, false, false),
  ('FOLDER_LIEN_RELEASE', 'Lien Release', null, 'FOLDER_LIEN_RELEASE', null, false, false),
  ('ESCALATE_OVER_10000', 'OVER-10000', null, 'ESCALATE', 'Over 10000', true, false),
  ('ESCALATE_MULTI_INVOICE_PDF', 'MULTI-INVOICE-PDF', null, 'ESCALATE', 'Multi PDF Invoice', false, false),
  ('ESCALATE_MULTI_PDF_MERGE', 'MULTI-PDF-MERGE', null, 'ESCALATE', 'Multi PDF Merge', false, false),
  ('ESCALATE_LIEN_WAIVER', 'LIEN-WAIVER', null, 'ESCALATE', 'Lien Waiver', false, false),
  ('ESCALATE_LINK_ONLY', 'LINK-ONLY', null, 'ESCALATE', 'Link Only', true, false),
  ('ESCALATE_0_DOLLAR_INVOICE', '0-DOLLAR-INVOICE', null, 'ESCALATE', '0 Dollar Invoice', false, false),
  ('ESCALATE_WRONG_FILE_TYPE', 'WRONG-FILE-TYPE', null, 'ESCALATE', 'Wrong File Type', false, false),
  ('ESCALATE_CONTRACT_PAY_APP', 'CONTRACT-PAY-APP', null, 'ESCALATE', 'Contract Pay App', false, false),
  ('ESCALATE_CREDIT_MEMO', 'CREDIT-MEMO', null, 'ESCALATE', 'Credit Memo', false, false),
  ('ESCALATE_CONTRACTOR_TIMESHEET', 'CONTRACTOR-TIMESHEET', null, 'ESCALATE', 'Contractor Timesheet', false, false),
  ('ESCALATE_VENDOR_QUESTION', 'VENDOR-QUESTION', null, 'ESCALATE', 'Vendor Question', false, false),
  ('ESCALATE_WRONG_DESTINATION', 'WRONG-DESTINATION', null, 'ESCALATE', 'Wrong Destination', false, false),
  ('ESCALATE_PAST_DUE', 'PAST-DUE', null, 'ESCALATE', 'Past Due', true, false),
  ('ESCALATE_DUPLICATE_SUSPECTED', 'DUPLICATE-SUSPECTED', null, 'ESCALATE', 'Duplicate Suspected', false, false),
  ('ESCALATE_SPLIT_MULTI_PDF', 'SPLIT-MULTI-PDF', null, 'ESCALATE', 'SPLIT-MULTI-PDF', false, false),
  ('ESCALATE_UNMATCHED_BUILDING', 'UNMATCHED-BUILDING', null, 'ESCALATE', 'Unmatched Building', false, false),
  ('ESCALATE_SPECIAL_ADDRESS', 'SPECIAL-ADDRESS', null, 'ESCALATE', 'Special Address', false, false),
  ('ESCALATE_CHECK_REQUEST', 'CHECK-REQUEST', null, 'ESCALATE', 'Check Request', false, false),
  ('ESCALATE_GENERAL', 'Escalate General', null, 'ESCALATE', 'General', false, false),
  ('NO_ACTION', 'No External Action', null, 'NO_ACTION', null, false, false)
on conflict (destination_code) do update
set display_name = excluded.display_name,
    email_address = excluded.email_address,
    parent_folder = excluded.parent_folder,
    label = excluded.label,
    send_teams_message = excluded.send_teams_message,
    send_email = excluded.send_email,
    updated_at = now();

insert into runtime_config (config_key, config_value, description)
values
  ('app_env', '"LOCAL"'::jsonb, 'Default local runtime environment.'),
  ('confidence_threshold', '0.90'::jsonb, 'Minimum extraction confidence for automatic routing.'),
  ('property_match_top_n', '5'::jsonb, 'Number of fuzzy property candidates retrieved per invoice.'),
  ('property_match_min_score', '0.45'::jsonb, 'Minimum trigram similarity score required for deterministic property match gating.'),
  ('amount_review_threshold', '10000'::jsonb, 'Invoices above this amount escalate unless their normal destination is Medius Properties.'),
  ('statement_outcome', '"FILE"'::jsonb, 'Default local statement handling outcome.'),
  ('default_escalate_destination', '"ESCALATE_GENERAL"'::jsonb, 'Default destination for escalation.')
on conflict (config_key) do update
set config_value = excluded.config_value,
    description = excluded.description,
    updated_at = now();

delete from runtime_config where config_key = 'dry_run';

-- Canonical asset and ownership seed data exported from live local Postgres apautomation on 2026-05-20.
delete from asset;
delete from ownership;

insert into ownership (ownership, destination, created_at) VALUES ('Alliance Outpost One', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Artemis', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Bell Helicopter', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('BK', 'ESCALATE_UNMATCHED_BUILDING', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Cambridge Outerloop', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('CFT - NV', 'MICHELE_FELLERS', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Hillwood', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('JPM', 'MICHELE_FELLERS', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Lexington', 'PATTIE_MCCLEAN', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Manulife', 'ESCALATE_UNMATCHED_BUILDING', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Nuveen', 'TIFFANY_BECK', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('O''Reillys Auto Parts', 'ESCALATE_UNMATCHED_BUILDING', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Scout', 'MICHELE_FELLERS', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Stonepeak', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('Tishman Realty', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('None', 'ESCALATE_UNMATCHED_BUILDING', '2026-05-20 08:37:41.833548');
insert into ownership (ownership, destination, created_at) VALUES ('CoFW', 'MEDIUS_PROPERTIES', '2026-05-20 08:37:41.833548');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (137, 'Dick''s Sporting Goods - Risinger', 'None', 'Industrial', 'Project Cats', 'Fort Worth', NULL, NULL, '10001 Old Burleson Road, Fort Worth, Texas 76140', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (138, 'Facebook Data Center', 'None', 'Other', NULL, 'AllianceTexas', 'Gateway North', NULL, '4500 Like Way, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (139, 'Frost Bank Alliance', 'None', 'Retail', NULL, 'AllianceTexas', NULL, NULL, '3000 Golden Triangle Boulevard, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (140, 'H-E-B Alliance', 'None', 'Retail', 'HEB', NULL, NULL, NULL, '3451 Heritage Trace Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (141, 'Hideaway Pizza', 'None', 'Retail', NULL, 'AllianceTexas', 'ATC North', NULL, '9800 North Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (142, 'Pinstack', 'None', 'Retail', NULL, 'AllianceTexas', NULL, NULL, '3650 Parish Lane, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (143, 'QuikTrip Alliance Crossing', 'None', 'Retail', NULL, 'AllianceTexas', NULL, NULL, '13401 Crossing Way, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (144, 'Roanoke Police Department', 'None', 'Other', 'Roanoke PD', 'AllianceTexas', NULL, NULL, '203 Fairway Drive, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (145, 'Test HCRM Assets', 'None', 'Communities', 'Test - HCRM Asset', 'Dallas', 'Alliance Center East', NULL, '2929 Carlisle Street, Dallas, Texas 75204', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (146, 'Texas Oncology', 'None', 'Retail', NULL, 'AllianceTexas', 'ATC North', NULL, '9750 Hillwood Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (147, 'Alliance Commerce Center 14', 'Alliance Outpost One', 'Office', 'ACC 14', 'AllianceTexas', 'Alliance Commerce Center', 'BW Gas & Convenience Holdings, LLC;ConGlobal Industries, LLC;Texhoma Land Consultants;Textron, Inc.', '2301 Eagle Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (148, 'Alliance Gateway 19', 'Artemis', 'Industrial', 'GW19', 'AllianceTexas', 'Gateway South', 'FedEx Supply Chain', '13500 Independence Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (149, 'Alliance Gateway 4', 'Artemis', 'Industrial', 'GW4', 'AllianceTexas', 'Gateway South', NULL, '13601 Independence Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (150, 'Alliance Gateway 5', 'Artemis', 'Industrial', 'GW5', 'AllianceTexas', 'Gateway South', 'FedEx Supply Chain', '13550 Independence Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (151, 'Alliance Gateway 72', 'Artemis', 'Industrial', 'GW72', 'AllianceTexas', 'Gateway North', 'Colgate Palmolive', '4800 Henrietta Creek Road, Fort Worth, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (152, 'Alliance Westport 9', 'Artemis', 'Industrial', 'WP9', 'AllianceTexas', 'Westport', 'Performance Team, A Maersk Company;United States Postal Service', '400 Intermodal Parkway, Haslet, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (153, 'Alliance Center North 10', 'Bell Helicopter', 'Industrial', 'ACN10', 'AllianceTexas', 'Alliance Center North', 'Bell Helicopter Textron, Inc.', '15100 North Beach Street, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (154, 'Westport 7', 'BK', 'Industrial', 'WP7', 'AllianceTexas', 'Westport', NULL, '700 Westport Parkway, Haslet, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (155, 'Heritage Commons III', 'Cambridge Outerloop', 'Office', 'HC3', 'AllianceTexas', 'Alliance Center', 'Amentum', '13601 North Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (156, 'Heritage Commons X', 'CFT - NV', 'Office', 'HCX', 'AllianceTexas', 'Alliance Center', 'Mercedes-Benz Financial Services', '14372 Heritage Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (157, 'City of Fort Worth Maintenance Base', 'CoFW', 'Aviation', 'CoFW MB', 'AllianceTexas', 'Alliance Center', 'AVX Defense Technologies;Alliance AI No. 1, LLC;GDC/Aspire;Gridiron Air;ILOAJP, LLC Holdings;MTU;Mammoth;O''Neill''s;Omni Air International;Paramount Aerospace Systems', '2070 Eagle Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (158, '2050 Roanoke Road', 'Hillwood', 'Office', 'CT West', 'AllianceTexas', 'Circle T Ranch', 'Kiewit Corporation', '2050 Roanoke Road, Westlake, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (159, 'ABP - Aviator Way', 'Hillwood', 'Aviation', 'ABP', 'AllianceTexas', 'Alliance Center', 'AVX Defense Technologies;Aerolane;Caterpillar, Inc.;ETD Development, LLC;Executive Jet Management;FBO Partners / Alliance Aviation Services;Gridiron Air;HGB Holdings, LLC;Halbert & Associates;Martin-Baker;Oshman Aviation Group LLC;Penske Jet Inc. Division;Trinity Broadcasting Network', '13850 Heritage Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (160, 'ACC5 Parking Lot ', 'Hillwood', 'Parking', 'ACC5', 'AllianceTexas', 'Alliance Commerce Center', 'Group O, Inc.', '2101 Eagle Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (161, 'Alliance AI (Maintenance Base)', 'Hillwood', 'Parking', 'Alliance AI', 'AllianceTexas', 'Alliance Center', 'Gatik;Wallport Transit Xpress', '2000 Eagle Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (162, 'Alliance Air Trade Center', 'Hillwood', 'Industrial', 'AATC', 'AllianceTexas', 'Alliance Center', 'Spartan Carrier Group', '1104 Tradewind Drive, Haslet, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (163, 'Alliance Center East 1', 'Hillwood', 'Industrial', 'ACE1', 'AllianceTexas', 'Alliance Center East', 'Target Corporation', '13750 North Freeway, Fort Worth, Texas', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (164, 'Alliance Center East 2', 'Hillwood', 'Industrial', 'ACE2', 'AllianceTexas', 'Alliance Center East', 'SGS Studios', '2601 Spirit Dr, Fort Worth, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (165, 'Alliance Center East 3', 'Hillwood', 'Industrial', 'ACE3', 'AllianceTexas', 'Alliance Center East', 'SGS Studios', '2701 Spirit Dr, Fort Worth, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (166, 'Alliance Center East Parking Lot', 'Hillwood', 'Parking', 'ACE PL', 'AllianceTexas', 'Alliance Center East', NULL, '2601 Spirit Drive, Fort Worth, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (167, 'Alliance Center No. 1', 'Hillwood', 'Aviation', 'AC1', 'AllianceTexas', 'Alliance Center', 'Embraer Aircraft Maintenance Services', '13537 Heritage Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (168, 'Alliance Center North 15', 'Hillwood', 'Industrial', 'ACN15', 'AllianceTexas', 'Alliance Center North', 'Enovis;Smart Warehousing, LLC', '3300 Eagle Parkway, Roanoke, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (169, 'Alliance Center North 2', 'Hillwood', 'Industrial', 'ACN2', 'AllianceTexas', 'Alliance Center North', 'Walmart', '15101 Beach Street, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (170, 'Alliance Center North 3', 'Hillwood', 'Industrial', 'ACN3', 'AllianceTexas', 'Alliance Center North', 'Callaway Golf Company', '15221 North Beach Street, Roanoke, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (171, 'Alliance Center North 4', 'Hillwood', 'Industrial', 'ACN4', 'AllianceTexas', 'Alliance Center North', 'Callaway Golf Company;Celestica', '15301 North Beach Street, Roanoke, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (172, 'Alliance Center North 5', 'Hillwood', 'Ground Lease', 'ACN5/DDF5', 'AllianceTexas', 'Alliance Center North', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (173, 'Alliance Center North 7', 'Hillwood', 'Industrial', 'ACN7', 'AllianceTexas', 'Alliance Center North', 'Henry Schein, Inc.', '3701 Litsey Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (174, 'Alliance Center North 8 ', 'Hillwood', 'Industrial', 'ACN8', 'AllianceTexas', 'Alliance Center North', 'W.W. Grainger', '15350 North Beach Street, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (175, 'Alliance Center North 9', 'Hillwood', 'Industrial', 'ACN9', 'AllianceTexas', 'Alliance Center North', 'Carolina Beverage Group, LLC', '15250 North Beach Street, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (176, 'Alliance Center North No. 6', 'Hillwood', 'Industrial', 'ACN6', 'AllianceTexas', 'Alliance Center North', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (177, 'Alliance Gateway 24', 'Hillwood', 'Industrial', 'GW24', 'AllianceTexas', 'Gateway South', 'Penske Logistics;Winner, LLC', '13550 Park Vista Boulevard, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (178, 'Alliance Gateway 25', 'Hillwood', 'Industrial', 'GW25', 'AllianceTexas', 'Gateway South', 'CEVA Logistics', '4500 Liberty Way, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (179, 'Alliance Gateway 34', 'Hillwood', 'Industrial', 'GW34', 'AllianceTexas', 'Gateway South', NULL, '5000 Westport Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (180, 'Alliance Gateway 57', 'Hillwood', 'Industrial', 'GW57', 'AllianceTexas', 'Gateway North', 'Carolina Beverage Group, LLC;Cummins Clean Fuel', '1051 Republic Drive, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (181, 'Alliance Gateway 63 Parking', 'Hillwood', 'Parking', 'GW63', 'AllianceTexas', 'Gateway North', 'Martin-Brower Company, LLC', '501 Patriot Parkway, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (182, 'Alliance Gateway 70', 'Hillwood', 'Industrial', 'GW70', 'AllianceTexas', 'Gateway North', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (183, 'Alliance Gateway 71', 'Hillwood', 'Industrial', 'GW71', 'AllianceTexas', 'Gateway North', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (184, 'Alliance Gateway 88', 'Hillwood', 'Industrial', 'GW88', 'AllianceTexas', 'Gateway North', 'Heritage Bag Company', '501 Gateway Parkway, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (185, 'Alliance Town Center', 'Hillwood', 'Retail', 'ATC', 'AllianceTexas', 'ATC South', NULL, '3108 Texas Sage Trail, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (186, 'Alliance Westport 11', 'Hillwood', 'Industrial', 'WP11', 'AllianceTexas', 'Westport', 'Walmart', '14700 Blue Mound Road, Haslet, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (187, 'Alliance Westport 12', 'Hillwood', 'Industrial', 'WP12', 'AllianceTexas', 'Westport', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (188, 'Alliance Westport 15', 'Hillwood', 'Industrial', 'WP15', 'AllianceTexas', 'Westport', NULL, '1401 Intermodal Parkway, Fort Worth, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (189, 'Alliance Westport 16', 'Hillwood', 'Industrial', 'WP16', 'AllianceTexas', 'Westport', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (190, 'Alliance Westport 24', 'Hillwood', 'Industrial', 'WP24', 'AllianceTexas', 'Westport', 'Stellar Energy', '15060 Blue Mound Road, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (191, 'Alliance Westport 25', 'Hillwood', 'Industrial', 'WP25', 'AllianceTexas', 'Westport', 'Southwire Company, LLC', '33.0010176938229 -97.3379401275164', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (192, 'ATCN Grocer Retail - Parkside East', 'Hillwood', 'Retail', 'Parkside East', 'AllianceTexas', 'ATC North', 'Black Rifle Coffee Company;CAVA;Candle Nail Spa;Chewy, Inc.;Dave''s Hot Chicken;Hash Kitchen;Magnolia Soap and Bath Co.;Mo'' Bettah''s Hawaiian Style Food;Nothing Bundt Cakes;Petbar Inc.;The Sicilian Butcher;Torchy''s Tacos', '3251 Heritage Trace Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (193, 'ATCN Retail 1 (Chi/Pei Wei)', 'Hillwood', 'Retail', 'Chi/Pei', 'AllianceTexas', 'ATC North', 'Chipotle Mexican Grill Inc;Pei Wei Asian Diner', '2901 Heritage Trace Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (194, 'ATCN Retail No. 2 - Parkside', 'Hillwood', 'Retail', 'Parkside', 'AllianceTexas', 'ATC North', 'First Watch Restaurants, Inc.;Hopdoddy Burger Bar;MOD Super Fast Pizza, LLC;Shack Enterprises, Inc.', '3101 Heritage Trace Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (195, 'Bank of America Harvest Ground Lease', 'Hillwood', 'Ground Lease', 'Harvest GL', 'Harvest', 'Harvest', 'Bank of America', '1226 Fm 407, Argyle, Texas 76226', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (196, 'Bluestem Village', 'Hillwood', 'Multifamily', 'Bluestem', 'AllianceTexas', 'ATC North', NULL, '10401 N Riverside Dr, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (197, 'Bonham and Baker', 'Hillwood', 'Multifamily', 'UL4', 'Frisco', 'Frisco Station', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (198, 'Caterpillar Ground Lease', 'Hillwood', 'Ground Lease', 'AC2', 'AllianceTexas', 'Alliance Center', 'Caterpillar, Inc.', '13501 Heritage Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (199, 'Chuy''s ATC Ground Lease', 'Hillwood', 'Ground Lease', 'GL ATC No.4 ', 'AllianceTexas', 'ATC North', 'Chuys Opco, Inc', '9700 North Freeway, Fort Worth, Texas 76137', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (200, 'Chuy''s Harvest Ground Lease', 'Hillwood', 'Ground Lease', 'Chuy''s GL', 'Harvest', 'Harvest', 'Chuys Opco, Inc', '1226 Fm 407, Argyle, Texas 76226', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (201, 'Corporate Line of Credit', 'Hillwood', 'Other', 'LOC', NULL, NULL, NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (202, 'Courtyard Marriott Alliance', 'Hillwood', 'Hotel', 'Courtyard', 'AllianceTexas', 'ATC North', NULL, '3001 Amador Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (203, 'Embraer Ground Lease', 'Hillwood', 'Ground Lease', 'AC16', 'AllianceTexas', 'Alliance Center East', 'Embraer Aircraft Maintenance Services', '2040 Eagle Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (204, 'Firebirds Ground Lease', 'Hillwood', 'Ground Lease', 'GL ATC No.6', 'AllianceTexas', 'ATC North', 'Firebirds Wood Fired Grill', '2900 Amador Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (205, 'Flight Test Center', 'Hillwood', 'Other', NULL, 'AllianceTexas', 'Northport', 'Autonomous Solutions, Inc.;Gatik;Helicopter Institute;Unmanned Experts;Wing Aviation', '10364 Harmonson Road, Justin, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (206, 'Freeport Business Center I', 'Hillwood', 'Office', 'Freeport 1', 'Irving', 'Irving', 'Boeing Distribution Services, LLC;Rushmore Loan Management Services;Yardi Systems, Inc.', '8616 Freeport Parkway, Irving, Texas 75063', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (207, 'Freeport Business Center II', 'Hillwood', 'Office', 'Freeport 2', 'Irving', 'Irving', 'Northrop Grumman Corporation', '8710 Freeport Parkway, Irving, Texas 75063', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (208, 'Freeport Business Center III', 'Hillwood', 'Office', 'Freeport 3', 'Irving', 'Irving', 'Sirius XM Connected Vehicle Services, Inc.', '8550 Freeport Parkway, Irving, Texas 75063', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (209, 'Frisco Station Infrastructure', 'Hillwood', 'Other', NULL, NULL, NULL, NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (210, 'Front 44 Retail', 'Hillwood', 'Retail', 'F44', 'AllianceTexas', 'Circle T Ranch', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (211, 'Gateway Logistics', 'Hillwood', 'Other', 'Torc', 'AllianceTexas', 'Alliance Center', 'Torc Robotics, Inc.', '13119 Old Denton Road, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (212, 'Gulfstream Ground Lease', 'Hillwood', 'Ground Lease', 'ACGA21', 'AllianceTexas', 'Alliance Center', 'Gulfstream Aerospace Corp.', '14601 Heritage Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (213, 'Hangar 7', 'Hillwood', 'Aviation', 'Hangar 7', 'AllianceTexas', 'Alliance Center', NULL, '1401 Intermodal Parkway, Fort Worth, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (214, 'Harvest House', 'Hillwood', 'Multifamily', 'Harvest House', 'Harvest', NULL, NULL, '200 Harvest Way, Argyle, Texas 76226', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (215, 'Harvest Town Center', 'Hillwood', 'Retail', 'HTC', 'Harvest', 'Harvest', 'European Wax Center;Gen Nail Salon;Gideon Math & Reading;Great Clips;Heartland Dental;Jersey Mike''s;Mo''Bettahs;Tom Thumb', NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (216, 'Hillwood Commons I', 'Hillwood', 'Office', 'HWC1', 'AllianceTexas', 'ATC North', 'AUI Partners, LLC;Amrock;AspenRidge Wealth Advisors;Autonomous Solutions, Inc.;Cargill Meat Solutions Corporation;Draken International;FirstService Residential;Hillwood Alliance Group, LP;Legacy Medical Consultants, LLC;Leidos;Peloton Land Solutions, Inc., a Westwood Company;Regus;Westinghouse Air Brake Technologies Corporation (Wabtec)', '9800 Hillwood Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (217, 'Hillwood Commons II', 'Hillwood', 'Office', 'HWC2', 'AllianceTexas', 'ATC North', 'ABI Commercial, L.P.;Amentum Services, Inc.;Burgess & Niple;CornerStone Staffing;Highland Homes;LJA Engineering;Lucid Private Offices - Alliance - Company, LLC', '9900 Hillwood Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (218, 'HWCC No. 2 (Corral City Retail)', 'Hillwood', 'Retail', 'Corral City', 'AllianceTexas', 'Corral City', 'LiquorLand Investments', '1213 Fm 407, Argyle, Texas 76226', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (219, 'HWCC No. 3 (Corral City RV Park)', 'Hillwood', 'Parking', 'Corral City', 'AllianceTexas', 'Corral City', NULL, '14007 Corral City Drive, Argyle, Texas 76226', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (220, 'Northlake Corners', 'Hillwood', 'Other', '1171/35W', 'AllianceTexas', NULL, NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (221, 'Offices 3 at Frisco Station', 'Hillwood', 'Other', NULL, NULL, NULL, NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (222, 'Paloma Village', 'Hillwood', 'Multifamily', 'Paloma', 'AllianceTexas', 'ATC South', NULL, '9100 Feather Grass Lane, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (223, 'PF Chang''s Ground Lease', 'Hillwood', 'Ground Lease', 'GL ATC No.3', 'AllianceTexas', 'ATC North', 'PF Chang''s', '2949 Amador Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (224, 'Portable 3', 'Hillwood', 'Office', 'PB3', 'AllianceTexas', 'Alliance Center', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (225, 'SageStone Village', 'Hillwood', 'Multifamily', 'SageStone', 'AllianceTexas', 'ATC South', NULL, '3255 Sagestone Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (226, 'SageWater Village', 'Hillwood', 'Multifamily', 'SageWater', 'AllianceTexas', 'ATC South', NULL, '9340 Feather Grass Lane, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (227, 'SageWood Village', 'Hillwood', 'Multifamily', 'SageWood', 'AllianceTexas', 'ATC South', NULL, '9100 General Worth Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (228, 'Tallgrass Village', 'Hillwood', 'Multifamily', 'Tallgrass', 'AllianceTexas', 'ATC North', NULL, '3350 Amador Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (229, 'The Crockett', 'Hillwood', 'Multifamily', 'Beach / 170 MF', 'AllianceTexas', NULL, NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (230, 'The Reverie', 'Hillwood', 'Multifamily', 'ATCS5', 'AllianceTexas', 'ATC South', NULL, '8499 North Riverside Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (231, 'Trinity at Pomona', 'Hillwood', 'Multifamily', 'Pomona', 'Houston', NULL, NULL, '4714 Orchard Creek Lane, Manvel, Texas 77578', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (232, 'Truck Yard Ground Lease', 'Hillwood', 'Ground Lease', 'TY', 'AllianceTexas', 'ATC South', 'Truck Yard', '3101 Prairie Vista Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (233, 'Union House', 'Hillwood', 'Multifamily', 'Union House', 'Little Elm', 'Little Elm', NULL, '4177 Gazebo Street, Little Elm, Texas 76227', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (234, 'Westport 27 Parking Lot', 'Hillwood', 'Parking', 'WP27', 'AllianceTexas', 'Westport', 'Heritage Fleet Parking', '1198 Intermodal Parkway, Fort Worth, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (235, 'Westport Container Depot', 'Hillwood', 'Other', 'Westport 31', 'AllianceTexas', 'Westport', NULL, NULL, '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (236, 'Alliance Gateway 14', 'JPM', 'Industrial', 'GW14', 'AllianceTexas', 'Gateway South', 'MP Materials', '4750 Alliance Gateway Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (237, 'Alliance Gateway 16', 'JPM', 'Industrial', 'GW16', 'AllianceTexas', 'Gateway South', NULL, '4700 Alliance Gateway Freeway, Fort Worth, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (238, 'Alliance Gateway 18', 'JPM', 'Industrial', 'GW18', 'AllianceTexas', 'Gateway South', 'Carolina Beverage Group, LLC', '13300 Park Vista Boulevard, Fort Worth, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (239, 'Alliance Gateway 31', 'JPM', 'Industrial', 'GW31', 'AllianceTexas', 'Gateway North', 'US Conec', '5201 Alliance Gateway Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (240, 'Alliance Gateway 51', 'JPM', 'Industrial', 'GW51', 'AllianceTexas', 'Gateway North', 'General Mills', '4901 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (241, 'Alliance Gateway 54', 'JPM', 'Industrial', 'GW54', 'AllianceTexas', 'Gateway North', 'Alliance Sports Group, LP;Ryder Integrated Logistics Inc.', '700 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (242, 'Alliance Gateway 55', 'JPM', 'Industrial', 'GW55', 'AllianceTexas', 'Gateway North', 'Samsung HVAC America, LLC', '776 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (243, 'Alliance Gateway 60', 'JPM', 'Industrial', 'GW60', 'AllianceTexas', 'Gateway North', 'American Tire Distributors', '300 Freedom Drive, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (244, 'Alliance Westport 3', 'JPM', 'Industrial', 'WP3', 'AllianceTexas', 'Westport', 'Airborne Tactical Advantage Company;Logistics Plus', '920 Westport Parkway, Haslet, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (245, 'Alliance Westport 6', 'JPM', 'Industrial', 'WP6', 'AllianceTexas', 'Westport', 'S.C. Johnson & Son, Inc', '850 Transport Drive, Haslet, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (246, 'Heritage Commons II', 'JPM', 'Office', 'HC2', 'AllianceTexas', 'Alliance Center', 'Crossland Construction Company, Inc.;North Tarrant Infrastructure', '13601 North Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (247, 'Alliance Northport 1', 'Lexington', 'Industrial', 'NP1', 'AllianceTexas', 'Northport', 'Schluter Systems, LP', '8363 East Sam Lee Lane, Northlake, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (248, 'Alliance Northport 3', 'Lexington', 'Industrial', 'NP3', 'AllianceTexas', 'Northport', 'Black & Decker', '8601 East Sam Lee Lane, Northlake, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (249, 'LEX Carrollton ', 'Lexington', 'Industrial', 'LEX Carrollton', 'Carrollton', 'Carrollton', 'Teasdale Foods', '2115 East Belt Line Road, Carrollton, Texas 75006', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (250, 'LEX Dallas', 'Lexington', 'Industrial', 'LEX Dallas', 'Dallas', NULL, 'Owens Corning', '3737 Duncanville Road, Dallas, Texas 75236', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (251, 'Alliance Gateway 11', 'Manulife', 'Industrial', 'GW11', 'AllianceTexas', 'Gateway South', 'Walmart', '5300 Westport Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (252, 'Alliance Commerce Center 1', 'Nuveen', 'Industrial', 'ACC 1', 'AllianceTexas', 'Alliance Commerce Center', 'Recaro Aircraft Seating Americas, Inc', '2275 Eagle Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (253, 'Alliance Commerce Center 2', 'Nuveen', 'Industrial', 'ACC 2', 'AllianceTexas', 'Alliance Commerce Center', 'Recaro Aircraft Seating Americas, Inc', '15001 Peterson Court, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (254, 'Alliance Commerce Center 4', 'Nuveen', 'Industrial', 'ACC 4', 'AllianceTexas', 'Alliance Commerce Center', 'LG Electronics Alabama, Inc;Lash OpCo;Recaro Aircraft Seating Americas, Inc', '2153 Eagle Parkway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (255, 'Alliance Gateway 15', 'Nuveen', 'Industrial', 'GW15', 'AllianceTexas', 'Gateway South', 'IES Commercial & Industrial, Inc.;Monitronics (Brinks)', '4800 Alliance Gateway Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (256, 'Alliance Gateway 22', 'Nuveen', 'Industrial', 'GW22', 'AllianceTexas', 'Gateway South', 'McKesson Corporation', '13501 Park Vista Boulevard, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (257, 'Alliance Gateway 23', 'Nuveen', 'Industrial', 'GW23', 'AllianceTexas', 'Gateway South', 'S&B Industry / Foxconn', '13301 Park Vista Boulevard, Fort Worth, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (258, 'Alliance Gateway 27', 'Nuveen', 'Industrial', 'GW27', 'AllianceTexas', 'Gateway North', NULL, '5601 Alliance Gateway Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (259, 'Alliance Gateway 49', 'Nuveen', 'Industrial', 'GW49', 'AllianceTexas', 'Gateway North', 'General Motors;Quanxin Lighting & Electrical', '899 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (260, 'Alliance Gateway 52', 'Nuveen', 'Industrial', 'GW52', 'AllianceTexas', 'Gateway North', 'W.W. Grainger', '5001 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (261, 'Alliance Gateway 58', 'Nuveen', 'Industrial', 'GW58', 'AllianceTexas', 'Gateway North', 'Animal Health International;Group O, Inc.;Sunbelt Rentals', '800 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (262, 'Alliance Gateway 62', 'Nuveen', 'Industrial', 'GW62', 'AllianceTexas', 'Gateway North', 'Martin-Brower Company, LLC', '400 Patriot Parkway, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (263, 'Alliance Gateway 9', 'Nuveen', 'Industrial', 'GW9', 'AllianceTexas', 'Gateway South', 'CEVA Logistics;Leidos', '5300 Alliance Gateway Freeway, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (264, 'Alliance Westport 1', 'Nuveen', 'Industrial', 'WP1', 'AllianceTexas', 'Westport', 'The Coca-Cola Company', '901 Railhead Drive, Haslet, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (265, 'Alliance Westport 4', 'Nuveen', 'Industrial', 'WP4', 'AllianceTexas', 'Westport', NULL, '125 Intermodal Parkway, Haslet, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (266, 'Westport 20', 'O''Reillys Auto Parts', 'Industrial', 'WP20', 'AllianceTexas', 'Westport', NULL, '1200 Intermodal Parkway, Fort Worth, Texas 76052', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (267, 'Alliance Gateway 50', 'Scout', 'Industrial', 'GW50', 'AllianceTexas', 'Gateway North', 'Tom Thumb;US LBM', '743 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (268, '1005 Railhead Drive', 'Stonepeak', 'Industrial', NULL, 'AllianceTexas', 'Westport', 'Kraft Heinz Foods Company', '1005 Railhead Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (269, '1006 Railhead Drive', 'Stonepeak', 'Industrial', NULL, 'AllianceTexas', 'Westport', 'Kraft Heinz Foods Company', '1006 Railhead Drive, Fort Worth, Texas 76177', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (270, 'Alliance Gateway 53', 'Stonepeak', 'Industrial', 'GW53', 'AllianceTexas', 'Gateway North', 'Bridgestone Americas Tire Operations', '501 Henrietta Creek Road, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (271, 'Alliance Gateway 61', 'Stonepeak', 'Industrial', 'GW61', 'AllianceTexas', 'Gateway North', 'Bridgestone Americas Tire Operations', '600 Gateway Parkway, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');
insert into asset (id, asset_name, ownership, asset_type, asset_alias, market_name, market_area, tenants, address, created_at) VALUES (272, 'Alliance Center North 1', 'Tishman Realty', 'Industrial', 'ACN1', 'AllianceTexas', 'Alliance Center North', 'LG Electronics Alabama, Inc', '14901 North Beach Street, Roanoke, Texas 76262', '2026-05-20 08:51:27.713258-05');

select setval('asset_id_seq', coalesce((select max(id) from asset), 1), true);

insert into workflow_rules (
  rule_code,
  rule_name,
  priority,
  enabled,
  condition_type,
  outcome,
  destination_code,
  reason_template,
  effective_start,
  version
)
values
  ('hard_multi_invoice_pdf', 'Multi-invoice PDF requires manual split', 100, true, 'document_flag', 'ESCALATE', 'ESCALATE_MULTI_INVOICE_PDF', 'Attachment appears to contain multiple invoices -> ESCALATE with MULTI-INVOICE-PDF label', '2026-05-07', 1),
  ('hard_separate_lien_waiver', 'Invoice with separate related backup requires escalation', 110, true, 'document_flag', 'ESCALATE', 'ESCALATE_LIEN_WAIVER', 'Invoice has separate related backup documentation -> ESCALATE with LIEN-WAIVER label', '2026-05-27', 1),
  ('hard_invoice_plus_lien_waiver', 'Superseded invoice plus lien waiver merge rule', 111, false, 'document_flag', 'ESCALATE', 'ESCALATE_MULTI_PDF_MERGE', 'Superseded by hard_separate_lien_waiver', '2026-05-07', 2),
  ('hard_wrong_file_type', 'Image, Word, or Excel attachment requires escalation', 115, true, 'attachment_extension', 'ESCALATE', 'ESCALATE_WRONG_FILE_TYPE', 'Attachment file type is not supported for AP invoice routing -> ESCALATE with WRONG-FILE-TYPE label', '2026-05-13', 1),
  ('hard_pdf_required_unreadable', 'Required invoice PDF is unreadable', 117, true, 'pre_decision_fact', 'ESCALATE', 'ESCALATE_GENERAL', 'Required invoice PDF could not be deterministically read -> ESCALATE', '2026-05-13', 1),
  ('hard_pdf_text_low_quality', 'Invoice PDF text quality is low', 118, true, 'pre_decision_fact', 'ESCALATE', 'ESCALATE_GENERAL', 'Deterministic PDF text quality is too low for safe routing -> ESCALATE', '2026-05-13', 1),
  ('hard_link_only_invoice', 'Link-only invoice requires escalation', 120, true, 'document_flag', 'ESCALATE', 'ESCALATE_LINK_ONLY', 'Invoice is only available by link -> ESCALATE with LINK-ONLY label', '2026-05-07', 1),
  ('hard_contractor_timesheet_no_invoice', 'Contractor timesheet without invoice requires escalation', 125, true, 'document_flag', 'ESCALATE', 'ESCALATE_CONTRACTOR_TIMESHEET', 'Contractor timesheet or time-detail document has no invoice in the run -> ESCALATE with CONTRACTOR-TIMESHEET label', '2026-06-04', 1),
  ('hard_contract_or_pay_app', 'Contract or pay application requires escalation', 130, true, 'document_type', 'ESCALATE', 'ESCALATE_CONTRACT_PAY_APP', 'High-risk document type requires human escalation -> ESCALATE with CONTRACT-PAY-APP label', '2026-05-07', 1),
  ('hard_credit_memo', 'Credit memo requires escalation', 135, true, 'document_type', 'ESCALATE', 'ESCALATE_CREDIT_MEMO', 'LLM classified current item as credit memo -> ESCALATE with CREDIT-MEMO label', '2026-06-17', 1),
  ('hard_vendor_inquiry', 'Vendor question or payment inquiry requires escalation', 140, true, 'document_flag', 'ESCALATE', 'ESCALATE_VENDOR_QUESTION', 'Vendor inquiry requires research or response -> ESCALATE with VENDOR-QUESTION label', '2026-05-07', 1),
  ('hard_wrong_destination', 'Wrong destination reply requires escalation', 142, true, 'document_flag', 'ESCALATE', 'ESCALATE_WRONG_DESTINATION', 'Recipient reports wrong destination -> ESCALATE with WRONG-DESTINATION label', '2026-05-20', 1),
  ('hard_past_due_notice', 'Past due invoice notice requires escalation', 119, true, 'document_flag', 'ESCALATE', 'ESCALATE_PAST_DUE', 'Past due or overdue invoice notice -> ESCALATE with PAST-DUE label', '2026-05-15', 1),
  ('hard_mixed_item_destinations', 'Multiple document items disagree on routing', 148, true, 'aggregation_mixed_destinations', 'ESCALATE', 'ESCALATE_SPLIT_MULTI_PDF', 'Multiple extracted document items resolved to different outcomes or destinations -> ESCALATE with SPLIT-MULTI-PDF label', '2026-05-27', 2),
  ('hard_no_action_email_pattern', 'Automated non-AP notification requires no action', 112, true, 'email_pattern_match', 'DISCARD', 'NO_ACTION', 'Matched configured non-AP automated email pattern -> DISCARD', '2026-05-13', 1),
  ('hard_current_reply_no_action', 'Short current reply requires no AP action', 114, true, 'current_reply_no_action', 'DISCARD', 'NO_ACTION', 'Short current reply contains acknowledgement or social reply only -> DISCARD', '2026-06-02', 1),
  ('appointment_informational_notice', 'Appointment informational notice requires no action', 116, true, 'observed_fact', 'DISCARD', 'NO_ACTION', 'LLM classified current email as informational appointment notice -> DISCARD', '2026-06-03', 1),
  ('duplicate_candidate', 'Duplicate candidate requires escalation', 200, true, 'duplicate_check', 'ESCALATE', 'ESCALATE_DUPLICATE_SUSPECTED', 'Duplicate candidate found in audit history -> ESCALATE with DUPLICATE-SUSPECTED label', '2026-05-07', 1),
  ('check_request_medius_property', 'Check request for Medius property routes to Medius', 250, true, 'check_request_property_routing', 'AUTO', null, 'Check request matched configured Medius property destination -> AUTO', '2026-05-27', 1),
  ('hard_check_request', 'Check request requires escalation', 260, true, 'document_type', 'ESCALATE', 'ESCALATE_CHECK_REQUEST', 'Check request requires human escalation -> ESCALATE', '2026-05-12', 1),
  ('informational_property_notice', 'Informational property notice routes to property destination', 350, true, 'informational_property_notice', 'AUTO', null, 'Informational property notice matched configured property destination -> AUTO', '2026-05-15', 1),
  ('amount_zero_invoice', 'Zero-dollar invoice requires escalation', 360, true, 'amount_equals_zero', 'ESCALATE', 'ESCALATE_0_DOLLAR_INVOICE', 'Invoice amount is zero and normal destination would auto-route -> ESCALATE with 0-DOLLAR-INVOICE label', '2026-06-15', 1),
  ('asset_type_multifamily', 'Multifamily asset routes to Medius MF', 375, true, 'property_asset_type', 'AUTO', 'MEDIUS_MF', 'Matched asset type is Multifamily -> AUTO to Medius MF', '2026-06-11', 3),
  ('amount_over_threshold', 'Invoice amount over configured threshold without qualifying project number exemption', 400, true, 'amount_threshold', 'ESCALATE', 'ESCALATE_OVER_10000', 'Invoice amount exceeds configured threshold and normal destination is not Medius Properties with project number -> ESCALATE with OVER-10000 label', '2026-05-27', 2),
  ('statement_file', 'Statement or account summary is filed', 500, true, 'document_type', 'FILE', 'FOLDER_STATEMENTS', 'Statement or account summary -> FILE', '2026-05-07', 1),
  ('ach_notice_file', 'ACH or auto-draft notice is filed', 520, true, 'document_type', 'FILE', 'FOLDER_ACH', 'ACH or auto-draft notice -> FILE', '2026-05-07', 1),
  ('ben_e_keith_notice_file', 'Ben E Keith notice is filed', 113, true, 'document_flag', 'FILE', 'FOLDER_BEN_E_KEITH', 'Ben E Keith notice -> FILE', '2026-05-07', 1),
  ('bill_to_mf', 'Multifamily bill-to routes to Medius MF', 610, false, 'bill_to_business_unit', 'AUTO', 'MEDIUS_MF', 'Bill-to indicates Multifamily -> AUTO', '2026-05-07', 1),
  ('property_routing_match', 'Property routing table match', 700, true, 'property_routing_match', 'AUTO', null, 'Property matched configured routing destination -> AUTO', '2026-05-07', 1),
  ('hard_unmatched_building', 'Unmatched building requires escalation', 750, true, 'property_unmatched', 'ESCALATE', 'ESCALATE_UNMATCHED_BUILDING', 'Property signal present but building is unmatched in routing table -> ESCALATE with UNMATCHED-BUILDING label', '2026-05-13', 1),
  ('confidence_below_threshold', 'Low confidence requires escalation', 800, true, 'confidence_threshold', 'ESCALATE', 'ESCALATE_GENERAL', 'Confidence below configured threshold -> ESCALATE', '2026-05-07', 1),
  ('fallback_escalate', 'No deterministic route matched', 900, true, 'fallback', 'ESCALATE', 'ESCALATE_GENERAL', 'No deterministic routing rule matched -> ESCALATE', '2026-05-07', 1)
on conflict (rule_code) do update
set rule_name = excluded.rule_name,
    priority = excluded.priority,
    enabled = excluded.enabled,
    condition_type = excluded.condition_type,
    outcome = excluded.outcome,
    destination_code = excluded.destination_code,
    reason_template = excluded.reason_template,
    effective_start = excluded.effective_start,
    version = excluded.version,
    updated_at = now();

insert into workflow_rule_conditions (rule_code, condition_key, condition_value)
values
  ('hard_multi_invoice_pdf', 'flag', '"multi_invoice_pdf"'::jsonb),
  ('hard_separate_lien_waiver', 'flag', '"separate_lien_waiver"'::jsonb),
  ('hard_invoice_plus_lien_waiver', 'flag', '"invoice_plus_lien_waiver"'::jsonb),
  ('hard_wrong_file_type', 'disallowed_extensions', '[".jpg", ".jpeg", ".png", ".doc", ".docx", ".xls", ".xlsx"]'::jsonb),
  ('hard_wrong_file_type', 'exempt_document_types', '["ach_notice", "auto_draft_notice", "ben_e_keith_notice"]'::jsonb),
  ('hard_wrong_file_type', 'exempt_document_flags', '["ach_or_auto_draft", "ben_e_keith"]'::jsonb),
  ('hard_pdf_required_unreadable', 'fact_key', '"pdf_required_but_unreadable"'::jsonb),
  ('hard_pdf_required_unreadable', 'expected', 'true'::jsonb),
  ('hard_pdf_text_low_quality', 'fact_key', '"pdf_text_low_quality"'::jsonb),
  ('hard_pdf_text_low_quality', 'expected', 'true'::jsonb),
  ('hard_link_only_invoice', 'flag', '"link_only_invoice"'::jsonb),
  ('hard_contractor_timesheet_no_invoice', 'flag', '"contractor_timesheet_no_invoice"'::jsonb),
  ('hard_contract_or_pay_app', 'document_types', '["contract", "pay_application"]'::jsonb),
  ('hard_credit_memo', 'document_types', '["credit_memo"]'::jsonb),
  ('hard_vendor_inquiry', 'flag', '"vendor_inquiry"'::jsonb),
  ('hard_wrong_destination', 'flag', '"wrong_destination"'::jsonb),
  ('hard_past_due_notice', 'flag', '"past_due"'::jsonb),
  ('hard_mixed_item_destinations', 'aggregation_reason', '"mixed_item_destinations"'::jsonb),
  ('hard_no_action_email_pattern', 'pattern_source', '"no_action_email_patterns"'::jsonb),
  ('hard_current_reply_no_action', 'max_chars', '320'::jsonb),
  ('hard_current_reply_no_action', 'require_quoted_history', 'true'::jsonb),
  ('hard_current_reply_no_action', 'allowed_sender_domains', '["hillwood.com"]'::jsonb),
  ('appointment_informational_notice', 'fact_key', '"indicates_informational_appointment_notice"'::jsonb),
  ('appointment_informational_notice', 'expected', 'true'::jsonb),
  ('appointment_informational_notice', 'document_types', '["unknown"]'::jsonb),
  ('appointment_informational_notice', 'blocked_flags', '["link_only_invoice", "missing_invoice_attachment", "vendor_inquiry", "wrong_destination", "past_due", "statement_or_account_summary", "ach_or_auto_draft", "ben_e_keith", "contract_or_pay_application", "lien_release_related", "conflicting_signals", "low_text_quality"]'::jsonb),
  ('appointment_informational_notice', 'forbid_source_attachments', 'true'::jsonb),
  ('duplicate_candidate', 'duplicate_statuses', '["suspected"]'::jsonb),
  ('check_request_medius_property', 'document_types', '["check_request"]'::jsonb),
  ('check_request_medius_property', 'allowed_destination_codes', '["MEDIUS_PROPERTIES"]'::jsonb),
  ('hard_check_request', 'document_types', '["check_request"]'::jsonb),
  ('informational_property_notice', 'document_types', '["unknown"]'::jsonb),
  ('informational_property_notice', 'blocked_flags', '["link_only_invoice", "vendor_inquiry", "past_due", "contract_or_pay_application", "conflicting_signals", "low_text_quality"]'::jsonb),
  ('amount_zero_invoice', 'document_types', '["invoice"]'::jsonb),
  ('amount_over_threshold', 'runtime_config_key', '"amount_review_threshold"'::jsonb),
  ('amount_over_threshold', 'exempt_destination', '"MEDIUS_PROPERTIES"'::jsonb),
  ('amount_over_threshold', 'exempt_requires_project_number', 'true'::jsonb),
  ('statement_file', 'document_types', '["statement", "account_summary"]'::jsonb),
  ('ach_notice_file', 'document_types', '["ach_notice", "auto_draft_notice"]'::jsonb),
  ('ben_e_keith_notice_file', 'flag', '"ben_e_keith"'::jsonb),
  ('bill_to_mf', 'business_unit_code', '"MF"'::jsonb),
  ('asset_type_multifamily', 'asset_type', '"Multifamily"'::jsonb),
  ('asset_type_multifamily', 'document_types', '["invoice"]'::jsonb),
  ('property_routing_match', 'requires_property_route', 'true'::jsonb),
  ('hard_unmatched_building', 'document_types', '["invoice", "unknown"]'::jsonb),
  ('confidence_below_threshold', 'runtime_config_key', '"confidence_threshold"'::jsonb),
  ('fallback_escalate', 'always', 'true'::jsonb)
on conflict (rule_code, condition_key) do update
set condition_value = excluded.condition_value;

delete from workflow_rule_conditions
where rule_code in (
  select rule_code from workflow_rules where condition_type = 'property_status'
);

delete from workflow_rule_conditions
where rule_code in ('alc_escalation', 'bill_to_alc');

update workflow_rules
set enabled = false,
    effective_end = coalesce(effective_end, current_date),
    updated_at = now()
where rule_code in ('alc_escalation', 'bill_to_alc');

delete from workflow_rules wr
where wr.rule_code in ('alc_escalation', 'bill_to_alc')
  and not exists (select 1 from decisions d where d.matched_rule_code = wr.rule_code)
  and not exists (select 1 from workflow_rule_versions wrv where wrv.rule_code = wr.rule_code);

update routing_destinations
set active = false,
    updated_at = now()
where destination_code in ('ESCALATE_ALC', 'MEDIUS_ALC');

delete from routing_destinations rd
where rd.destination_code in ('ESCALATE_ALC', 'MEDIUS_ALC')
  and not exists (select 1 from actions a where a.destination_code = rd.destination_code)
  and not exists (select 1 from decisions d where d.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rules wr where wr.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rule_versions wrv where wrv.destination_code = rd.destination_code)
  and not exists (select 1 from ownership o where o.destination = rd.destination_code)
  and not exists (select 1 from asset_custom ac where ac.destination_code = rd.destination_code);

update routing_destinations
set active = false,
    updated_at = now()
where destination_code = 'ESCALATE_MULTIFAMILY';

delete from routing_destinations rd
where rd.destination_code = 'ESCALATE_MULTIFAMILY'
  and not exists (select 1 from actions a where a.destination_code = rd.destination_code)
  and not exists (select 1 from decisions d where d.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rules wr where wr.destination_code = rd.destination_code)
  and not exists (select 1 from workflow_rule_versions wrv where wrv.destination_code = rd.destination_code)
  and not exists (select 1 from ownership o where o.destination = rd.destination_code)
  and not exists (select 1 from asset_custom ac where ac.destination_code = rd.destination_code);
delete from workflow_rules where condition_type = 'property_status';

insert into no_action_email_patterns (
  pattern_name,
  sender_email_equals,
  sender_domain_equals,
  subject_regex,
  body_regex,
  reason_template,
  priority,
  enabled,
  effective_start
)
values
  (
    'proofpoint_end_user_digest',
    'noreply-digest@hillwood.com',
    'hillwood.com',
    '^Spam\s+\d+U:',
    null,
    'Proofpoint end-user digest notification with no AP routing action required',
    100,
    true,
    '2026-05-13'
  )
on conflict (pattern_name) do update
set sender_email_equals = excluded.sender_email_equals,
    sender_domain_equals = excluded.sender_domain_equals,
    subject_regex = excluded.subject_regex,
    body_regex = excluded.body_regex,
    reason_template = excluded.reason_template,
    priority = excluded.priority,
    enabled = excluded.enabled,
    effective_start = excluded.effective_start,
    updated_at = now();

insert into workflow_rule_versions (
  rule_code,
  version,
  rule_name,
  priority,
  enabled,
  condition_type,
  condition_snapshot,
  outcome,
  destination_code,
  reason_template,
  effective_start,
  effective_end
)
select
  wr.rule_code,
  wr.version,
  wr.rule_name,
  wr.priority,
  wr.enabled,
  wr.condition_type,
  coalesce(
    jsonb_object_agg(wrc.condition_key, wrc.condition_value) filter (where wrc.condition_key is not null),
    '{}'::jsonb
  ) as condition_snapshot,
  wr.outcome,
  wr.destination_code,
  wr.reason_template,
  wr.effective_start,
  wr.effective_end
from workflow_rules wr
left join workflow_rule_conditions wrc on wrc.rule_code = wr.rule_code
group by wr.rule_code
on conflict (rule_code, version) do update
set rule_name = excluded.rule_name,
    priority = excluded.priority,
    enabled = excluded.enabled,
    condition_type = excluded.condition_type,
    condition_snapshot = excluded.condition_snapshot,
    outcome = excluded.outcome,
    destination_code = excluded.destination_code,
    reason_template = excluded.reason_template,
    effective_start = excluded.effective_start,
    effective_end = excluded.effective_end;

