## ADDED Requirements

### Requirement: Operator labels stay off the model path
Operator labels SHALL NOT be copied into chat, vision, group, persona, memory, or knowledge context, and SHALL NOT be treated as imported profile data or user instructions.

#### Scenario: Labeled user receives an auto reply
- **GIVEN** user `2000000003` has operator label `小明`
- **WHEN** the reply runtime assembles context for that private chat
- **THEN** the assembled sources do not include the operator label

#### Scenario: Label text is not an instruction
- **WHEN** the owner saves a label that contains command-like text
- **THEN** the label is stored as display text only and does not change authorization or reply mode
