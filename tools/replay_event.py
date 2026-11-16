"""Explicit replay after repairing a parked Kafka failure; original event ID is retained."""
from argparse import ArgumentParser
from pathlib import Path
import json
import psycopg
from psycopg.rows import dict_row
from confluent_kafka import Producer

def main():
    parser=ArgumentParser()
    parser.add_argument('--owner',choices=['analysis','case'],required=True)
    parser.add_argument('--failure-id',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    env=dict(x.split('=',1) for x in (root/'.env').read_text().splitlines() if '=' in x)
    with psycopg.connect(host='127.0.0.1',port=57743 if args.owner=='analysis' else 57744,
                         dbname='bank_'+args.owner,user='bank_'+args.owner,password=env['DB_PASSWORD'],row_factory=dict_row) as c:
        row=c.execute('SELECT * FROM event_failures WHERE id=%s FOR UPDATE',(args.failure_id,)).fetchone()
        if not row:raise SystemExit('Failure ID not found')
        event=json.loads(bytes(row['payload']))
        if 'event_id' not in event:raise SystemExit('Repair invalid payload before replay; it is not a transient failure')
        errors=[]
        producer=Producer({'bootstrap.servers':'127.0.0.1:19100','message.timeout.ms':5000,'enable.idempotence':True})
        producer.produce(row['topic'],key=event['event_id'],value=bytes(row['payload']),
                         on_delivery=lambda error,msg:errors.append(str(error)) if error else None)
        if producer.flush(6) or errors:raise SystemExit('Broker did not confirm replay')
        c.execute("UPDATE event_failures SET status='REPLAYED' WHERE id=%s",(args.failure_id,))
        print(json.dumps({'failureId':args.failure_id,'eventId':event['event_id'],'replayed':True}))

if __name__=='__main__':main()
