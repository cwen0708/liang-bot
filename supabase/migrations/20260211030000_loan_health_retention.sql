-- loan_health: 每次 INSERT 後自動刪除超過 3 天的舊資料
CREATE OR REPLACE FUNCTION prune_loan_health() RETURNS trigger AS $$
BEGIN
  DELETE FROM loan_health WHERE created_at < now() - interval '3 days';
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prune_loan_health
  AFTER INSERT ON loan_health
  FOR EACH STATEMENT
  EXECUTE FUNCTION prune_loan_health();
