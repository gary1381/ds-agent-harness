# Term resolver probe (exploratory, O3)

Held-out paraphrases (set 3, `paraphrases_test.json`): 160 (none of them used for training or for the term edits). Training: 160 tuning paraphrases plus each metric's label and definition. Classifier: character 1-4-gram TF-IDF, logistic regression, abstains below p = 0.5.

| resolver | right | another metric (false-hold risk) | none (no check) | right, en | right, zh |
|---|---|---|---|---|---|
| regex | 111 (69%) | 11 | 38 | 57 | 54 |
| classifier | 78 (49%) | 1 | 81 | 50 | 28 |
| regex, then classifier | 122 (76%) | 12 | 26 | 63 | 59 |
