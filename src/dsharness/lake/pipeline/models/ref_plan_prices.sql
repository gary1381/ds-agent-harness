-- Price book (fen). Owned by Finance; plus rose 5900 -> 6900 on 2026-08-01.
SELECT * FROM (VALUES
    ('basic', 2900, DATE '2000-01-01', DATE '9999-12-31'),
    ('plus',  5900, DATE '2000-01-01', DATE '2026-07-31'),
    ('plus',  6900, DATE '2026-08-01', DATE '9999-12-31'),
    ('pro',   9900, DATE '2000-01-01', DATE '9999-12-31')
) AS t(plan, price_fen, valid_from, valid_to)
