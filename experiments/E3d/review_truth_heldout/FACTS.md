# What the exports show, task by task

All numbers are computed from the published NYC TLC yellow-taxi exports, over trips whose pickup falls in the
month. "TLC indicators" are the TLC's own monthly industry indicators (trips per day, and farebox per day =
fares and surcharges without tips), published separately from the trip exports. Whether the pipeline team has
them is part of question 4 below.

| month | trips | vendor 1 | vendor 2 | other vendors | sum fare_amount | sum tip_amount | sum total_amount | trips/day vs TLC | farebox/day vs TLC |
|---|---|---|---|---|---|---|---|---|---|
| 2020-08 | 1,007,270 | 322,494 | 684,678 | 98 | 13,414,673.87 | 1,846,801.80 | 18,705,264.11 | −0.0% | −0.9% |
| 2020-09 | 1,340,883 | 414,183 | 916,254 | 10,446 | 16,430,045.35 | 2,584,641.69 | 23,628,417.88 | +0.0% | −1.0% |
| 2020-10 | 1,681,100 | 512,047 | 1,155,918 | 13,135 | 21,225,540.40 | 3,326,880.94 | 30,364,196.88 | +0.0% | −0.9% |
| 2022-10 | 3,675,320 | 1,048,824 | 2,621,162 | 5,334 | 56,232,154.62 | 10,508,699.28 | 81,766,806.48 | +0.3% | −0.1% |
| 2022-11 | 3,252,620 | 740,043 | 2,508,059 | 4,518 | 49,307,790.52 | 9,256,525.66 | 71,785,093.06 | −6.8% | −7.7% |
| 2022-12 | 3,399,503 | 942,003 | 2,457,064 | 436 | −120,179,992.19 | 188,307,092.75 | 83,464,628.09 | +0.3% | −243% |
| 2023-01 | 3,066,718 | 827,367 | 2,239,351 | 0 | 56,326,136.64 | 10,328,449.96 | 82,863,379.14 | +0.4% | +0.2% |
| 2023-02 | 2,913,900 | 787,837 | 2,124,862 | 1,201 | 53,092,373.97 | 9,863,053.15 | 78,379,498.41 | +0.4% | +0.3% |
| 2023-08 | 2,824,175 | 716,980 | 2,106,574 | 621 | 55,688,178.68 | 9,632,215.57 | 80,850,537.02 | +0.7% | +0.7% |
| 2023-09 | 2,846,690 | 731,968 | 2,113,870 | 852 | 58,844,184.08 | 10,320,073.89 | 84,779,876.06 | −8.9% | −8.7% |

## y2009: serve 2020-09 (routine; 2020-08 is served)
- The project builds 2020-09 READY as it is; every test passes.
- Trips are up 33% on August (the city reopening in 2020). Vendor 6 goes from 78 trips in August to 10,433 in
  September (0.8% of the month's trips); vendor 5 has 13.
- The lowest days are Sunday 6 and Monday 7 September (Labor Day), at 56% of the median day.

## y2010: fix 2020-10 (drift; 2020-08 and 2020-09 are served)
- The build fails on the raw-export check: `airport_fee` is typed INTEGER in the 2020-10 export instead of DOUBLE.
- `airport_fee` is empty on every trip of 2020-10. In 2020-08 and 2020-09 it is empty on all but 2 and 3 trips,
  which report 0.00, so the served sums of those months are 0.00. No other column changes.

## y2211: serve 2022-11 (routine; 2022-10 is served)
- The project builds 2022-11 READY as it is; every test passes (the reconciliation is a share of each vendor's
  trips, so it does not see missing trips).
- Vendor 1 reports almost no trips from Monday 14 to Sunday 20 November: 2, 3, 60, 71, 90, 82 and 222 trips a day,
  against 31,000–38,000 on the days before and after. Vendor 2 is between 2% lower and 12% higher than on
  the same weekdays a week later. The month's trips are 11.5% below October (8.5% per day).

## y2212: serve 2022-12 (routine; 2022-10 and 2022-11 are served)
- The project builds 2022-12 READY as it is; every test passes.
- Two vendor-2 trips on 14 December (payment_type 0, distance 0) carry fare_amount −133,391,414.00 with
  tip_amount +133,391,363.53, and fare_amount −44,463,768.92 with tip_amount +44,463,786.70. Their total_amount
  is −47.17 and 21.08, and their components add up to the total, so the reconciliation test passes.
- With them, the month's served fare_amount is −120,179,992.19 and tip_amount 188,307,092.75. Without them, fare
  would be 57,675,190.73 and tips 10,451,942.52, in line with the other months. total_amount is unaffected.

## y2302: fix 2023-02 (drift; 2023-01 is served)
- The build fails on the raw-export check. In the 2023-02 export `airport_fee` is spelled `Airport_fee`;
  VendorID, PULocationID and DOLocationID are 32-bit instead of 64-bit integers; passenger_count and RatecodeID are
  integers instead of doubles. The values mean the same.

## y2309: serve 2023-09 (routine; 2023-08 is served)
- The project builds 2023-09 READY as it is; every test passes.
- Friday 22, Saturday 23 and Sunday 24 September have 4,633, 5,732 and 4,813 trips, against 90,000–120,000 on the
  days around them; Thursday 21 has 41,939. Both vendors drop on those days.
- Four vendor-1 trips carry very large amounts: fares of 187,502.96, 143,163.45, 19,152.90 (with extra
  10,002.50) and 12,015.47. Together about 0.6% of the month's fares.
