from typing import cast

from backend.common.data import Repository, model
from backend.services.analysis.persistence.models import Profile, Result, Run, RuleVersion, RuleHit
from backend.services.analysis.profiles.public_metrics import public_metrics

class AnalysisRepository(Repository):

    def profile(self, id):
        row = cast("dict | None", self.db.one('SELECT * FROM behavior_profiles WHERE id=%s', (id,)))
        return model(Profile, {**row, "metrics": public_metrics(row["metrics"])} if row else None)

    def run(self, id):
        return model(Run, self.db.one('SELECT * FROM analysis_runs WHERE id=%s', (id,)))

    def result(self, id):
        row = self.db.one('SELECT * FROM analysis_results WHERE id=%s', (id,))
        if not row:
            return None
        hits = self.db.query('SELECT h.*,r.code AS rule_code,v.version FROM rule_hits h JOIN rule_versions v ON v.id=h.rule_version_id JOIN analysis_rules r ON r.id=v.rule_id WHERE h.result_id=%s ORDER BY r.code', (id,))
        values = [model(RuleHit, x) for x in hits]
        context = self.db.one('SELECT details FROM detection_context WHERE result_id=%s', (id,))
        for signal in (context['details'].get('behavioralSignals', []) if context else []):
            if signal['contribution']:
                values.append(RuleHit(rule_code=signal['code'], version=1, feature_value=signal['observed'],
                                      limit_value=signal['baseline'], contribution=signal['contribution']))
        return model(Result, row, hits=values)

    def rule_versions(self, id):
        return [model(RuleVersion, r) for r in self.db.query('SELECT * FROM rule_versions WHERE rule_id=%s ORDER BY version,id', (id,))]

    def run_results(self, id):
        rows = self.db.query('SELECT * FROM analysis_results WHERE run_id=%s ORDER BY transaction_id', (id,))
        return {str(r['transaction_id']): r for r in rows}

    def hit_codes(self, run):
        rows = self.db.query('SELECT a.transaction_id,r.code FROM analysis_results a JOIN rule_hits h ON h.result_id=a.id JOIN rule_versions v ON v.id=h.rule_version_id JOIN analysis_rules r ON r.id=v.rule_id WHERE a.run_id=%s ORDER BY a.transaction_id,r.code', (run,))
        out = {}
        for r in rows:
            out.setdefault(str(r['transaction_id']), set()).add(r['code'])
        contexts = self.db.query('SELECT a.transaction_id,d.details FROM analysis_results a JOIN detection_context d ON d.result_id=a.id WHERE a.run_id=%s', (run,))
        for row in contexts:
            for signal in row['details'].get('behavioralSignals', []):
                if signal['contribution']:
                    out.setdefault(str(row['transaction_id']), set()).add(signal['code'])
        return out

    def profiles(self, client, run, curr):
        return [model(Profile, r) for r in self.db.query('SELECT * FROM behavior_profiles WHERE client_id=%s AND run_id=%s AND currency=%s ORDER BY window_end,id', (client, run, curr))]
