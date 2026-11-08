# Project guidance

The user chose an ecommerce analysis assistant on 2026-11-05. Preserve the old
financial project and archive. Keep this project independent; do not import its
old architecture or dependencies without a documented reason.

Read `docs/project-plan.md`, `docs/engineering-process.md`, and the relevant
module research record before implementation. Use Chinese for user-facing
explanations and project decision records.

## Personal account and company isolation

The user explicitly requires this boundary because this is a company computer.
It takes precedence over convenience or defaults inherited from the environment.

- Work only in this independent local project and the user's explicitly
  identified personal GitHub repository. The previous personal finance project
  remains reference-only under the existing authorization.
- Do not access, inspect, clone, fetch, change, commit to, push to, or otherwise
  operate on company repositories, company GitLab, or company organization spaces.
- Scope every Git command to this project's absolute directory. Never scan the
  computer for other repositories or reuse a repository merely because it is the
  shell's default working directory.
- Before remote operations, verify the personal username, repository owner and
  exact remote destination. Stop dependent operations if ownership is ambiguous.
  The confirmed personal GitHub username is `hhhhao139-ai`. The browser verified
  the newly created public repository `https://github.com/hhhhao139-ai/commerce-insight`
  on 2026-11-08. See `docs/account-boundary.md` for publication status.
  Do not operate on any other owner or organization.
- Do not change global/system Git configuration, credential helpers, machine SSH
  settings, company sign-ins, browser profiles, or corporate authentication.
- Do not use an existing CLI login, Git identity, SSH key or stored credential
  without establishing that it belongs to the authorized personal account.
  Prefer the browser session the user personally signs into. Browser login does
  not authorize or establish a matching CLI login.
- The user enters passwords and verification codes directly in the browser.
  Do not request, read, print or store credentials in chat, files, logs or commits.
- Keep company code, internal documents, business data and credentials out of
  this project and any personal repository. Public release must only include
  reviewed project files and permitted public/sample data.

## Required working process

- Before implementing or optimizing a module, identify a concrete user problem,
  establish a baseline or reproducible counterexample, research comparable
  products using primary sources, compare minimal alternatives, explain the
  choice and validation plan, then implement and measure.
- A new project has no existing software defect until one is observed. Label
  hypothetical failures, manual examples, prototypes and measured results
  separately. Do not fabricate a before/after comparison.
- Record source URLs, access dates, versions or commits. Label rolling documents
  as unpinned. Hiring requirements are evidence of desired skills, not proof of
  a product's architecture or user demand.
- Communicate the rationale before coding. This is not a per-change approval
  requirement; proceed within authorized scope and ask only for material
  missing information or decisions.
- Keep baseline and experiment records. Use fixed data and metric definitions,
  disclose model, prompt, tool, runtime and budget changes, and never invent
  improvement percentages or deployment claims.

## Product and architecture constraints

- Java owns business services; Python owns Agent execution using LangGraph.
  The exact service, storage and scheduling choices require module decisions.
- Data is externally supplied. Confirm ambiguous mappings and enforce capability
  limits when inputs cannot support a requested analysis.
- Financial amounts and aggregations must be computed by deterministic tools.
  Evidence must distinguish observed associations from causal explanations.
- Establish a strong single-Agent baseline. Add specialist Agents only when
  controlled experiments justify the extra context, coordination and cost.
- Business authorization, SQL restrictions and budgets must be enforced by code,
  not only prompts. Do not add autonomous business write actions to the MVP.
- Public data licenses, code licenses and model/API terms are distinct. No raw
  private customer data, credentials or unlicensed datasets in the repository.
- Synthetic data validates engineering cases; it does not establish real-world
  usefulness or model quality. Real-model tests must be distinguished from mocks.
- The user currently has no pilot users or private data. Start with public data
  and reproducible samples; do not claim demand or operational impact is proven.
- Explain important decisions so the user can reproduce and discuss them in
  interviews. AI-assisted code must still be understood and verified.
