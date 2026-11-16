# Census delivery match: digest vs numbers (exploratory, delivery_match.py)

Attempts in harness arms whose final rows match no submission by digest.

| arm | what the agent's log shows | correct | attempts |
|---|---|---|---|
| H | no submission with these rows | True | 4 |

| arm | qid | agent | rep | correct | finding |
|---|---|---|---|---|---|
| H | F-H30 | codex | 2 | True | no submission with these rows |
| H | F-H30 | codex | 3 | True | no submission with these rows |
| H | F-L08 | codex | 3 | True | no submission with these rows |
| H | F-L31 | codex | 2 | True | no submission with these rows |
