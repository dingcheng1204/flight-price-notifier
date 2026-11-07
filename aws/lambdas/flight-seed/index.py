# Deploy-time only: base64-decodes a payload into S3 so zips/config can be landed
# without direct S3 upload access. Deployed via inline CloudFormation (stack "flight-seed").
import base64, boto3
def handler(e, c):
    boto3.client("s3").put_object(Bucket=e["bucket"], Key=e["key"], Body=base64.b64decode(e["b64"]), ContentType=e.get("ct","application/octet-stream"))
    return {"ok": True, "key": e["key"]}
