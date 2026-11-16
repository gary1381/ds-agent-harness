# Term coverage: `experiments/E4/context/semantic_model.yaml`

111 of 160 paraphrased questions resolve to their own metric (69%); 11 resolve to another metric (a right declaration would be contradicted: a false hold) and 38 to none (the declared metric is not checked against the question). Questions written by Haiku from each metric's label and definition, without the term lists (experiments/E4/term_coverage.py).

| metric | right, en | right, zh | another metric | none |
|---|---|---|---|---|
| active_customers | 6/8 | 4/8 | 0 | 6 |
| average_order_value | 8/8 | 7/8 | 1 | 0 |
| cancellation_rate | 5/8 | 5/8 | 2 | 4 |
| cancelled_value | 4/8 | 7/8 | 2 | 3 |
| gross_product_sales | 3/8 | 3/8 | 3 | 7 |
| net_product_sales | 8/8 | 8/8 | 0 | 0 |
| new_customers | 5/8 | 4/8 | 1 | 6 |
| orders | 7/8 | 5/8 | 0 | 4 |
| repeat_customers | 6/8 | 5/8 | 2 | 3 |
| units_sold | 5/8 | 6/8 | 0 | 5 |

## Misses

| metric | lang | question | resolved to |
|---|---|---|---|
| gross_product_sales | en | total sales value of goods shipped to shops last quarter in GBP? | net_product_sales |
| gross_product_sales | en | how much did the UK side bring in for product sales in Q2 | net_product_sales |
| gross_product_sales | en | yo whats our top line sales for 2024 | net_product_sales |
| gross_product_sales | en | what's our gross merchandise value for 2025 so far? | nothing |
| gross_product_sales | en | what's the sticker value of all items we invoiced in september | nothing |
| gross_product_sales | zh | 今年到现在总共卖了多少钱的货(英镑) | nothing |
| gross_product_sales | zh | 英国市场上个季度的总货值多少 | nothing |
| gross_product_sales | zh | 帮我查一下去年全年的GMV | nothing |
| gross_product_sales | zh | 9月开出去的所有货值合计是多少英镑 | nothing |
| gross_product_sales | zh | 2025年10月销售总额 gross 多少钱 | nothing |
| cancelled_value | en | what was the £ amount of cancellations in Q3? | nothing |
| cancelled_value | en | sum of cancelled amounts by month this year | nothing |
| cancelled_value | en | Value of orders that were cancelled in november, in pounds? | orders |
| cancelled_value | en | what's our cancellation total in GBP for 2025 | nothing |
| cancelled_value | zh | 被取消的那部分销售额有多少英镑 | net_product_sales |
| cancellation_rate | en | cancel rate for 2025 | nothing |
| cancellation_rate | en | cancellation ratio by month this year pls | nothing |
| cancellation_rate | en | how bad are cancels, as a percent of sales, in June | net_product_sales |
| cancellation_rate | zh | 英国市场上个季度销售额里有多大比例被取消了 | net_product_sales |
| cancellation_rate | zh | cancel rate 去年多少 | nothing |
| cancellation_rate | zh | 被取消的货占总销售的占比,6月 | nothing |
| orders | en | how many orderz did we have in oct | nothing |
| orders | zh | 英国市场二季度的单量是多少 | nothing |
| orders | zh | 9月份下了几单 | nothing |
| orders | zh | 这个月(11月)单子有多少个 | nothing |
| average_order_value | zh | 平均每笔订单的货值多少 | orders |
| units_sold | en | how many pieces shipped out in Q3 to UK shops | nothing |
| units_sold | en | how many gift items did we sell in september | nothing |
| units_sold | en | how many individual pieces did we sell last month | nothing |
| units_sold | zh | 英国市场三季度出货数量是多少 | nothing |
| units_sold | zh | 上个月出了多少件货 | nothing |
| active_customers | en | how many shops ordered from us in november | nothing |
| active_customers | en | count of buyers per month for 2025 | nothing |
| active_customers | zh | 二季度下单的不同客户有多少 | nothing |
| active_customers | zh | 9月活跃买家几个 | nothing |
| active_customers | zh | 去年有多少家店下过单 | nothing |
| active_customers | zh | 今年有过购买的客户总数 | nothing |
| new_customers | en | new cust. count for 2025 by month | nothing |
| new_customers | en | how many fresh customers came in in june | nothing |
| new_customers | en | newly acquired customers in the UK in 2024 | nothing |
| new_customers | zh | 9月首次下单的客户有几个 | nothing |
| new_customers | zh | 第一次在我们这买东西的客户, 6月多少 | nothing |
| new_customers | zh | 拉新多少,英国市场 2024年 | nothing |
| new_customers | zh | 新开发客户数 上季度 | active_customers |
| repeat_customers | en | how many of our regulars ordered in november | nothing |
| repeat_customers | en | how many old customers reordered in Sept | nothing |
| repeat_customers | zh | 英国市场上季度回购的客户数 | active_customers |
| repeat_customers | zh | 去年有多少老客人又来下单 | nothing |
| repeat_customers | zh | 重复下单的客户数, 9月 | active_customers |
