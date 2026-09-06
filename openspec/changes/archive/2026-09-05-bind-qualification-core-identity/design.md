# Design: Qualification core-identity bind

## Trust boundary

Compiled `CORE_IDENTITY.prompt_directives()` and `conversation_directives("qualification-synthetic")` are repository-owned product facts, not user data. Qualification still rejects `user_qq`, history, personas, and memories as inputs.

## Scoring

`evaluate_qualification_case` still walks the suite blocking codes. For `creator_identity`:

- Always fail on explicit contradiction tokens.
- Require a YCH / 维护者 marker only when the fixture declares `creator_identity`.

Weather, JSON, and greeting fixtures can follow the disclosure policy and still pass identity.

## Request assembly

Text capabilities (chat, vision, stats) prepend one system `ModelMessage` built by `qualification.identity`. Image generation stays prompt-only from the image fixture.

## Evidence

`qualification_check_results` already exists. The runner SHALL persist sanitized check rows when a case is scored, and case reads SHALL load them. Failed decisions SHOULD copy blocking reason codes from those rows.
