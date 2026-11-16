# Census delivery match: digest vs numbers (exploratory, delivery_match.py)

Attempts in harness arms whose final rows match no submission by digest.

| arm | what the agent's log shows | correct | attempts |
|---|---|---|---|
| H | numeric match, CONSISTENT/HOLD | True | 2 |
| H | numeric match, FLAGGED/REJECT | True | 4 |
| H | numeric match, VERIFIED+ALLOW | True | 7 |
| H1 | numeric match, CONSISTENT/HOLD | True | 5 |
| H1 | numeric match, FLAGGED/REJECT | True | 1 |

| arm | qid | agent | rep | correct | finding |
|---|---|---|---|---|---|
| H | C-H12 | codex | 1 | True | numeric match, VERIFIED+ALLOW |
| H | C-H28 | claude | 1 | True | numeric match, VERIFIED+ALLOW |
| H | C-H28 | claude | 2 | True | numeric match, VERIFIED+ALLOW |
| H | C-H28 | claude | 3 | True | numeric match, VERIFIED+ALLOW |
| H | C-H28 | codex | 2 | True | numeric match, VERIFIED+ALLOW |
| H | C-H28 | codex | 3 | True | numeric match, VERIFIED+ALLOW |
| H | C-H32 | claude | 1 | True | numeric match, FLAGGED/REJECT |
| H | C-H32 | claude | 2 | True | numeric match, FLAGGED/REJECT |
| H | C-H32 | claude | 3 | True | numeric match, FLAGGED/REJECT |
| H | C-H32 | codex | 1 | True | numeric match, FLAGGED/REJECT |
| H | C-L11 | codex | 1 | True | numeric match, CONSISTENT/HOLD |
| H | C-L11 | codex | 2 | True | numeric match, CONSISTENT/HOLD |
| H | C-L34 | codex | 1 | True | numeric match, VERIFIED+ALLOW |
| H1 | C-H32 | claude | 2 | True | numeric match, FLAGGED/REJECT |
| H1 | C-H32 | codex | 1 | True | numeric match, CONSISTENT/HOLD |
| H1 | C-H32 | codex | 2 | True | numeric match, CONSISTENT/HOLD |
| H1 | C-H32 | codex | 3 | True | numeric match, CONSISTENT/HOLD |
| H1 | C-L11 | codex | 1 | True | numeric match, CONSISTENT/HOLD |
| H1 | C-L11 | codex | 3 | True | numeric match, CONSISTENT/HOLD |
