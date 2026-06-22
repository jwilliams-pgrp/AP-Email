update workflow_rules
set
  destination_code = 'ESCALATE_MULTI_PDF_MERGE',
  reason_template = 'Invoice has separate related backup documentation -> ESCALATE with MULTI-PDF-MERGE label',
  updated_at = now()
where rule_code = 'hard_separate_lien_waiver';
