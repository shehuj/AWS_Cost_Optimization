import boto3

PROTECTED_TAG_KEY = "do_not_delete"
PROTECTED_TAG_VALUE = "true"

def is_protected(tags):
    if not tags:
        return False
    for tag in tags:
        if tag['Key'] == PROTECTED_TAG_KEY and tag['Value'] == PROTECTED_TAG_VALUE:
            return True
    return False

def cleanup_ec2():
    ec2 = boto3.client('ec2')

    reservations = ec2.describe_instances()['Reservations']
    for res in reservations:
        for instance in res['Instances']:
            if is_protected(instance.get('Tags', [])):
                print(f"Skipping protected EC2: {instance['InstanceId']}")
                continue

            print(f"Terminating EC2: {instance['InstanceId']}")
            ec2.terminate_instances(InstanceIds=[instance['InstanceId']])

def cleanup_s3():
    s3 = boto3.client('s3')

    buckets = s3.list_buckets()['Buckets']
    for bucket in buckets:
        name = bucket['Name']

        try:
            tagging = s3.get_bucket_tagging(Bucket=name)
            if is_protected(tagging.get('TagSet', [])):
                print(f"Skipping protected S3 bucket: {name}")
                continue
        except:
            pass

        print(f"Deleting S3 bucket: {name}")

        # Delete objects first
        objects = s3.list_objects_v2(Bucket=name)
        if 'Contents' in objects:
            for obj in objects['Contents']:
                s3.delete_object(Bucket=name, Key=obj['Key'])

        s3.delete_bucket(Bucket=name)

def cleanup_rds():
    rds = boto3.client('rds')

    dbs = rds.describe_db_instances()['DBInstances']
    for db in dbs:
        arn = db['DBInstanceArn']

        tags = rds.list_tags_for_resource(ResourceName=arn)['TagList']
        if is_protected(tags):
            print(f"Skipping protected RDS: {db['DBInstanceIdentifier']}")
            continue

        print(f"Deleting RDS: {db['DBInstanceIdentifier']}")
        rds.delete_db_instance(
            DBInstanceIdentifier=db['DBInstanceIdentifier'],
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True
        )

def cleanup_lambda():
    lam = boto3.client('lambda')

    functions = lam.list_functions()['Functions']
    for fn in functions:
        arn = fn['FunctionArn']
        tags = lam.list_tags(Resource=arn).get('Tags', {})

        if tags.get(PROTECTED_TAG_KEY) == PROTECTED_TAG_VALUE:
            print(f"Skipping protected Lambda: {fn['FunctionName']}")
            continue

        print(f"Deleting Lambda: {fn['FunctionName']}")
        lam.delete_function(FunctionName=fn['FunctionName'])

def skip_route53_domains():
    print("Skipping Route53 domains (protected by default)")

if __name__ == "__main__":
    print("🚨 Starting AWS cleanup...")

    cleanup_ec2()
    cleanup_s3()
    cleanup_rds()
    cleanup_lambda()
    skip_route53_domains()

    print("✅ Cleanup complete")