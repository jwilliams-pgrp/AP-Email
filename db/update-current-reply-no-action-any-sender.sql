-- Allow current-reply no-action routing for any sender domain.
-- @hillwood.com remains an extraction indicator only, not a deterministic routing gate.

begin;

delete from workflow_rule_conditions
where rule_code = 'hard_current_reply_no_action'
  and condition_key = 'allowed_sender_domains';

update workflow_rule_versions
set condition_snapshot = condition_snapshot - 'allowed_sender_domains'
where rule_code = 'hard_current_reply_no_action'
  and condition_snapshot ? 'allowed_sender_domains';

commit;
