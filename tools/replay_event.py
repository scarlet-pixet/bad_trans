"""Request owner-side replay and show the durable status of the request."""
from argparse import ArgumentParser
from getpass import getpass
import json
import os
from uuid import uuid4

import requests


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument("--owner",choices=["analysis","case"],required=True)
    parser.add_argument("--failure-id",required=True)
    parser.add_argument("--reason",required=True)
    parser.add_argument("--url",default="http://127.0.0.1:18100/graphql")
    parser.add_argument("--ca")
    parser.add_argument("--login",default="admin")
    parser.add_argument("--key",default=None,help="Reuse this key after a lost response")
    args=parser.parse_args()
    with requests.Session() as session:
        if args.ca:
            session.verify=args.ca

        def graphql(query, variables):
            reply=session.post(args.url,json=dict(query=query,variables=variables),timeout=20)
            reply.raise_for_status()
            result=reply.json()
            if result.get("errors"):
                raise SystemExit(result["errors"][0]["message"])
            return result["data"]

        login=graphql("mutation($l:String!,$p:String!){signIn(login:$l,password:$p){accessToken}}",
                      dict(l=args.login,p=os.getenv("REPLAY_GATEWAY_PASSWORD") or getpass("Gateway password: ")))
        session.headers["Authorization"]="Bearer "+login["signIn"]["accessToken"]
        try:
            key=args.key or str(uuid4())
            print(f"Request key: {key}",flush=True)
            result=graphql(
                'mutation($o:String!,$id:ID!,$r:String!,$k:String!){'
                'changeFailureReview(owner:$o,id:$id,action:"RETRY",reason:$r,idempotencyKey:$k)}',
                dict(o=args.owner,id=args.failure_id,r=args.reason,k=key),
            )["changeFailureReview"]
            print(json.dumps(dict(failureId=result["id"],eventId=result["eventId"],
                                  state=result["state"],proof=result["proof"]),ensure_ascii=False))
        finally:
            graphql("mutation{signOut}",{})


if __name__=="__main__":
    main()
