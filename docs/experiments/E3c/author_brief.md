# Brief for request authors (E3c v2 blind request set)

You are writing an evaluation set for testing AI data analysts. Each item is a request that someone in a retail
company sends to an analyst after an e-mail test. The analysts will get the test's data file, described below. They
must reply to the requester in writing, with numbers where numbers fit. You do not answer the requests, and you do
not see the data. You write only the requests.

## The company and the test

The company is a US apparel retailer that sells by phone and on the web. This spring it ran a two-week e-mail test.
The analysts receive this description of the data, word for word:

> `campaign.duckdb` holds one table, `email_test`: one row per customer, 64,000 customers who bought from us in the
> past twelve months. Each customer was assigned at random, with equal chance, to one of three groups: sent the
> men's merchandise e-mail, sent the women's merchandise e-mail, or sent no e-mail. Visits, purchases and spend
> were tracked for the two weeks after the e-mails went out.
>
> | column | description |
> |---|---|
> | recency | months since the last purchase (before the e-mail) |
> | history_segment | dollars spent in the past year, binned |
> | history | dollars spent in the past year |
> | mens | 1 = bought men's merchandise in the past year |
> | womens | 1 = bought women's merchandise in the past year |
> | zip_code | Urban, Surburban (sic) or Rural |
> | newbie | 1 = new customer in the past twelve months |
> | channel | channels the customer bought from in the past year: Phone, Web, Multichannel |
> | segment | assigned group: Mens E-Mail, Womens E-Mail, No E-Mail |
> | visit | 1 = visited the website in the two weeks after the e-mail |
> | conversion | 1 = bought in the two weeks after the e-mail |
> | spend | dollars spent in the two weeks after the e-mail |

## What to write

Write **20 requests**, as different from each other as you can. Write them the way real people in a retail company
write to their analyst:
- The requesters vary: a CMO, a campaign manager, a merchandiser, finance, the CRM team, a regional manager, an
  agency, a board member, a data-science lead.
- The purposes vary: deciding next season's campaign, a budget line, a board slide, targeting rules, a forecast, a
  post-mortem, checking a claim someone already made in a meeting, or a press or investor note.
- The wording varies: precise or vague; short, or with background; neutral, or assuming an answer the requester
  hopes for.
- Some ask for one number. Others ask for a recommendation, a comparison, a segment list, a projection or a yes/no.
- Some may ask for something the file cannot fully answer. Write those as the requester would, and do not mark
  them.
- If a request needs a business input the file does not hold, such as the cost of sending an e-mail or a margin,
  the requester states it in the request, as people do.
- Each request is at most 80 words, in English, and makes sense on its own. Do not refer to other requests.

Do not write SQL, answers or hints about the right method. Do not mention that this is a test of analysts.

## Output

Reply with ONLY a YAML list, nothing before or after it:

```yaml
- id: R01
  requester: "<role>"
  request: "<the request, in the requester's words>"
- id: R02
  ...
```
