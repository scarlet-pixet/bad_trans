ALTER TABLE review_cases
 ADD COLUMN latest_assessment_score SMALLINT CHECK(latest_assessment_score BETWEEN 0 AND 100),
 ADD COLUMN latest_assessment_status TEXT CHECK(latest_assessment_status IN ('COMPLETE','PARTIAL','UNAVAILABLE')),
 ADD COLUMN latest_assessment_policy_version TEXT,
 ADD CONSTRAINT case_assessment_consistent CHECK(
 (latest_assessment_status IS NULL AND latest_assessment_score IS NULL AND latest_assessment_policy_version IS NULL) OR
 (latest_assessment_status IS NOT NULL AND latest_assessment_policy_version IS NOT NULL AND
  ((latest_assessment_status='UNAVAILABLE' AND latest_assessment_score IS NULL) OR
   (latest_assessment_status IN ('COMPLETE','PARTIAL') AND latest_assessment_score IS NOT NULL))));
