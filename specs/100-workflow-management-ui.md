# 100 - Workflow Management UI Spec

## Purpose

Define the local management page behavior for process control display and Postgres-backed ownership management and asset lookup interactions.

This spec is pending redesign. New backend work must use `asset` and `ownership`; the existing property/address management UI must not drive schema compatibility requirements.

## Scope

The future management page must use asset terminology and the `/api/workflow/assets` backend surface.

Out of scope for this version:
- process toggle runtime service control

## UI Requirements

- The page shows Manage Ownership, Asset Custom, and Asset Lookup sections; legacy add property, manage address, and property management sections are not displayed.
- Manage Ownership reads and writes local Postgres `ownership` rows.
- Users can add new ownership rows with required `ownership` and `destination` fields only.
- Users can update the destination for an existing ownership row selected from the ownership dropdown.
- Destination is selected from a distinct dropdown list sourced from `routing_destinations.destination_code`.
- Asset Lookup is read-only.
- Asset Custom appears between Manage Ownership and Asset Lookup.
- Asset Custom reads and writes local Postgres `asset_custom` rows.
- Asset Custom displays Asset Name, Asset Alias, Address, Routing Destination, Comment, and Actions columns.
- Asset Custom add and edit are inline table-row workflows.
- Asset Custom routing destination is a required dropdown sourced from `/api/workflow/destinations`; free-text destination entry is not allowed.
- Asset Custom delete asks for confirmation and then physically deletes the row.
- Asset Lookup displays the dataset returned by `vw_asset_lookup`.
- Asset Lookup column headers use business-friendly asset and destination names.
- Asset Lookup does not display Asset Source, Lookup ID, or Comment fields even when the API returns them.
- A single Asset Lookup search input filters rows by matching any field.

## Safety and Local Behavior

- The page must not mutate external systems.
- The process toggle is UI-only and does not change runtime service behavior.
- The selected process toggle option is vertically centered; selected `On` is green and selected `Off` is red.
- Ownership add and destination updates persist to local Postgres `ownership` rows through `/api/workflow/ownership`.
- Ownership mutations write `management_audit_events` rows with `changed_table = 'ownership'`.
- Asset Custom mutations write `management_audit_events` rows with `changed_table = 'asset_custom'`.
- Asset Lookup does not mutate local data or external systems.

## Testing Requirements

Given current frontend tooling in this repo, acceptance validation for this increment is:
- React production build succeeds.
- Manual UI verification confirms toggle behavior, ownership add, ownership destination update, Asset Custom add/edit/delete, destination dropdown values including `ESCALATE_SPECIAL_ADDRESS`, and asset lookup search against persisted Postgres data.

When frontend test tooling is introduced, automated coverage must include:
- search across all Asset Lookup fields
- add ownership flow
- update ownership destination flow
- process toggle state change

## Acceptance Criteria

- Management page only shows process toggle, Manage Ownership, and Asset Lookup features defined in the redesigned spec.
- Process `On`/`Off` toggle switches visible state in UI.
- Manage Ownership reads from local Postgres `ownership` rows.
- Manage Ownership supports persisted add through API with only `ownership` and `destination` required.
- Manage Ownership supports persisted destination updates for selected existing ownership rows.
- Manage Ownership destination options are distinct values from `routing_destinations.destination_code`.
- Asset Lookup reads joined `asset`, `ownership`, and `routing_destinations` fields.
- Asset Lookup hides Asset Source, Lookup ID, and Comment in the table.
- Search input filters across every Asset Lookup field.
