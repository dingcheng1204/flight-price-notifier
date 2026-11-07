"""flight-parser-wrapper: EventBridge target.
Builds the set of routes people actually subscribe to (distinct origin/destination in the
subscriptions table) and fans out one async flight-parser invocation per route."""
import json, os
import boto3

_table = boto3.resource("dynamodb").Table("subscriptions")
_lambda = boto3.client("lambda")


def subscribed_routes():
    routes, kw = {}, {"ProjectionExpression": "origin, destination"}
    while True:
        page = _table.scan(**kw)
        for it in page.get("Items", []):
            o, d = it.get("origin"), it.get("destination")
            if o and d:
                routes[f"{o}-{d}"] = (o, d)
        if "LastEvaluatedKey" not in page:
            return routes
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def handler(event, context):
    routes = subscribed_routes()
    for route, (o, d) in sorted(routes.items()):
        payload = {"origin": o, "destination": d, "route": route}
        _lambda.invoke(FunctionName=os.environ.get("PARSER_FN", "flight-parser"),
                       InvocationType="Event", Payload=json.dumps(payload).encode())
        print("dispatched", route)
    return {"ok": True, "dispatched": len(routes)}
