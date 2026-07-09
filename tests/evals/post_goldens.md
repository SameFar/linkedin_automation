# Golden topics for draft evaluation

These eight topics are the fixed input set for `test_post_quality.py`. They are chosen to
stress different failure modes of the prompt, not to be representative of what the user
writes about.

Change this file only when you mean to change the eval. Adding a topic invalidates
comparison against previous runs; note the date and the reason below.

Each topic is a top-level `-` bullet. The indented note is for the human reading the
output, not for the model.

---

- Why most code review comments are about the wrong thing
  - General opinion. Baseline: does it sound like a person or like LinkedIn?

- What I got wrong about microservices in my first year
  - Invites a fabricated war story. The model has no such story. Watch for invented
    companies, invented headcounts, invented outages.

- The hidden cost of a 30-minute standup
  - Invites a fabricated statistic. There is no real number available to it. Watch for
    "teams waste 40% of..." style inventions.

- Hiring: the take-home test debate
  - Two-sided topic. Watch for both-sides mush that takes no position.

- Why I stopped using feature branches
  - First-person claim about a decision. The model must generalise rather than invent
    the specific migration it supposedly ran.

- Kubernetes is probably not your bottleneck
  - Contrarian technical claim. Watch for hedging into meaninglessness.

- Career advice I would give my younger self
  - Maximum cliché pressure. This is where "let that sink in" shows up if it is going
    to show up anywhere.

- Announcing that our team open-sourced an internal tool
  - Announcement shape, the natural home of "I'm thrilled to share". There is no real
    tool; the draft must stay generic rather than invent a name and a repo.
