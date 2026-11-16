-- Registered data incidents: the ONLY invoice rows the governed pipeline may correct.
-- Owned by Billing Ops with Finance sign-off; every row is an approved change ticket. A correction
-- applies to exactly one (region, month, invoice_type) scope and never outside it. schema.yml proves
-- the registration both ways: every in-scope charge was really affected (below the cheapest plan as
-- exported), and no charge outside a registered scope is off the price book. Only charge incidents
-- can be registered, because the price book is the reference that proves a charge was affected.
-- Agent pipeline changes cannot add or widen an incident (CI treats this file as frozen reference data).
SELECT * FROM (VALUES
    ('INC-2026-03-SOUTH', 'south', '2026-03', 'charge', 100,
     'billing gateway exported south-region charges in yuan instead of fen',
     'finance-controller@company (CHG-2026-0412)')
) AS t(incident_id, region, month, invoice_type, factor, reason, approved_by)
