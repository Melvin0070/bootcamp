# Architecture: Activity 04 — CloudFormation Cost Optimisation

## Problem Diagnosis

The original "lift and shift" template moved an on-prem web server to AWS
without re-architecting it. The result: a single `m5.4xlarge` running 24/7.

| Anti-pattern | Root cause | Monthly cost impact |
|---|---|---|
| `m5.4xlarge` hardcoded | Engineer copied on-prem server spec | $552.96 (100% of waste) |
| No Auto Scaling Group | Single `AWS::EC2::Instance` resource | Zero elasticity — pays peak capacity at all times |
| No lifecycle hook | Instances killed immediately on scale-in | In-flight requests dropped; data loss risk |
| No cost tags | No `Project` / `Environment` / `Owner` | Finance cannot attribute $552/mo to any team |
| SSH open to internet | Port 22 inbound from `0.0.0.0/0` | Attack surface for brute-force / credential theft |
| Hardcoded AMI ID | Region-specific string in Properties | Template breaks in any region other than us-east-1 |

---

## Architecture: Before

```
Internet
    │
    ▼
┌─────────────────────────────────┐
│  AWS::EC2::Instance             │
│  InstanceType: m5.4xlarge       │  ← $0.768/hr × 24h × 30d = $552.96/mo
│  Running 24/7, no scaling       │
│  No tags, no lifecycle          │
│  SSH open to 0.0.0.0/0          │
│  Hardcoded AMI: ami-0c02fb...   │
└─────────────────────────────────┘
         Single point of failure
         No cost attribution
         No graceful shutdown
```

---

## Architecture: After

```
Internet
    │  HTTP:80
    ▼
┌────────────────────────────────────────────────────────┐
│  AWS::AutoScaling::AutoScalingGroup                    │
│  Min: 0  │  Desired: 1  │  Max: 4                     │
│  VPCZoneIdentifier: [subnet-a, subnet-b]               │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │  AWS::EC2::LaunchTemplate                        │  │
│  │  InstanceType: !Ref InstanceType (t3.medium)     │  │  ← $0.0416/hr
│  │  ImageId: !Ref LatestAmiId (SSM-resolved)        │  │  ← works any region
│  │  IamInstanceProfile: WebServerInstanceProfile    │  │  ← no SSH needed
│  │  MetadataOptions: HttpTokens: required (IMDSv2)  │  │
│  │  Tags propagate to EC2 + EBS volumes             │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
         │                           │
         │ scale-in event            │ CPU target tracking
         ▼                           ▼
┌─────────────────────┐    ┌──────────────────────────────┐
│  LifecycleHook      │    │  ScalingPolicy               │
│  TERMINATING        │    │  TargetTrackingScaling       │
│  HeartbeatTimeout:  │    │  CPU target: 60%             │
│    300s (5 min)     │    └──────────────────────────────┘
│  DefaultResult:     │
│    CONTINUE         │    ┌──────────────────────────────┐
└────────┬────────────┘    │  ScheduledScaleDown          │
         │                 │  cron(0 19 ? * MON-FRI *)    │
         ▼                 │  DesiredCapacity: 0           │
┌────────────────────┐     ├──────────────────────────────┤
│  SNS Topic         │     │  ScheduledScaleUp            │
│  → SQS Queue       │     │  cron(0 7  ? * MON-FRI *)   │
│  (durable, 600s    │     │  DesiredCapacity: 1           │
│   retention)       │     └──────────────────────────────┘
└────────────────────┘
         │
         ▼
┌────────────────────┐
│  CloudWatch Alarm  │
│  CPU > 80% / 5min  │
│  All resources     │
│  tagged:           │
│   Project          │
│   Environment      │
│   Owner            │
└────────────────────┘
```

---

## Cost Comparison

All prices are us-east-1 on-demand. Savings are estimates based on a Mon–Fri
business hours workload (8h/day active, 16h/day scaled to zero).

| Scenario | Instance | Hours/day | $/hr | $/month |
|---|---|---|---|---|
| **Before (broken)** | m5.4xlarge | 24 | $0.768 | **$552.96** |
| After — dev (scale-to-zero) | t3.medium | 8 | $0.0416 | **$9.98** |
| After — staging (always-on 1) | t3.medium | 24 | $0.0416 | **$29.95** |
| After — prod (2 instances) | t3.large | 24 | $0.0832 | **$59.90** |

**Peak saving (dev workload): ~98% — from $553/mo to ~$10/mo.**

> The t3 family uses burstable CPU credits. A `t3.medium` accumulates credits
> during idle periods and spends them during traffic spikes — ideal for
> workloads with bursty patterns like web servers.

---

## Trade-off Table

| Decision | Chosen | Alternative | Trade-off |
|---|---|---|---|
| Instance family | t3 (burstable) | m5 (fixed performance) | t3 is ~18× cheaper for bursty workloads; m5 better for sustained 100% CPU (ML training, video encoding) |
| Scaling policy | Target tracking (CPU 60%) | Step scaling | Target tracking auto-tunes; step scaling needs manual threshold maintenance |
| Lifecycle hook timeout | 300s | 60s / 600s | 300s covers most request drains; too short = dropped requests; too long = slow scale-in |
| Scale-to-zero schedule | Mon–Fri 19:00–07:00 UTC | No schedule | Saves ~67% of hours; trade-off is cold-start latency on scale-up (60–90s for t3) |
| SSH access | Removed (SSM only) | Port 22 open | SSM Session Manager requires no inbound port, logs all sessions to CloudWatch |
| DefaultResult on hook | CONTINUE | ABANDON | CONTINUE allows termination to proceed if the app doesn't signal within 300s; ABANDON retries and can cause stuck instances |
| AMI resolution | SSM Parameter Store | Hardcoded AMI ID | SSM always resolves the latest Amazon Linux 2 AMI per region; hardcoded IDs break cross-region deploys |

---

## Lifecycle Hook Deep Dive

### What problem does it solve?

Without a lifecycle hook, when Auto Scaling decides to terminate an instance
(scale-in, health check failure, or scheduled action), it sends a
`SIGTERM` and terminates the instance within seconds. Any HTTP request
in flight is dropped. Connections to databases are forcibly closed.

### How it works

```
ASG decides to terminate instance
          │
          ▼
  LifecycleHook fires BEFORE termination
          │
          ▼
  Instance enters "Terminating:Wait" state
  (still running, not yet terminated)
          │
          ├── App server finishes in-flight requests
          ├── Drains connection pool
          ├── Flushes local cache to S3/DynamoDB
          │
          ▼
  Instance signals CONTINUE (or heartbeat expires → DefaultResult: CONTINUE)
          │
          ▼
  Instance terminates cleanly
```

### Heartbeat timeout

```
HeartbeatTimeout: 300   ← 5 minutes

If instance signals CONTINUE before timeout → terminates immediately.
If timeout expires → DefaultResult: CONTINUE → terminates anyway.
If DefaultResult: ABANDON → instance goes back to InService (dangerous — can loop).
```

### Real-world example: Shopify flash sales

During a Black Friday scale-in event, Shopify's checkout Lambda-equivalents use
lifecycle hooks with a 120-second heartbeat. The hook fires when ASG wants to
remove an instance, waits for the checkout service to finish all cart operations,
then signals CONTINUE. Without this, partial cart writes would corrupt order state.

---

## Auto Scaling Policies Explained

### Target Tracking (what we use)

```
You say: "Keep average CPU at 60%"
ASG says: "I'll add/remove instances automatically to hit that target"

Traffic spike → CPU rises above 60%
→ ASG launches new instance
→ CPU drops back toward 60%

Traffic drops → CPU falls below ~54% (10% hysteresis)
→ ASG terminates one instance (firing lifecycle hook)
→ CPU rises back toward 60%
```

**Advantage:** No manual scale-out / scale-in thresholds to tune.

### Scheduled Scaling (what we also use)

```
You say: "Every weekday at 19:00 UTC, set DesiredCapacity=0"
ASG says: "Got it — I'll scale to zero at 19:00 regardless of CPU"

This is predictable, deterministic, and costs nothing to run.
It's the biggest single cost lever in this template.

cron(0 19 ? * MON-FRI *)  ← scale down to 0
cron(0 7  ? * MON-FRI *)  ← scale up to 1
```

**Combined:** Target tracking handles intraday traffic spikes; scheduled actions
handle the overnight off-peak window. Together they cover both reactive and
predictive scaling needs.

---

## AWS Console Setup Guide

### Step 1: Find your VPC and Subnet IDs

```bash
# Default VPC
aws ec2 describe-vpcs \
  --filters "Name=isDefault,Values=true" \
  --query "Vpcs[0].VpcId" --output text

# Default subnets in that VPC
aws ec2 describe-subnets \
  --filters "Name=defaultForAz,Values=true" \
  --query "Subnets[].SubnetId" --output text
```

### Step 2: Deploy the stack

```bash
# Copy and fill in your values
cp .env.example .env
# Edit .env with your real VPC_ID, SUBNET_IDS, OWNER_EMAIL

source .env

aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name web-stack-$ENVIRONMENT \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    Environment=$ENVIRONMENT \
    InstanceType=$INSTANCE_TYPE \
    MinSize=$MIN_SIZE \
    MaxSize=$MAX_SIZE \
    DesiredCapacity=$DESIRED_CAPACITY \
    VpcId=$VPC_ID \
    SubnetIds=$SUBNET_IDS \
    OwnerEmail=$OWNER_EMAIL
```

### Step 3: Verify the ASG

```bash
ASG_NAME=$(aws cloudformation describe-stacks \
  --stack-name web-stack-dev \
  --query "Stacks[0].Outputs[?OutputKey=='ASGName'].OutputValue" \
  --output text)

aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names $ASG_NAME \
  --query "AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity,Instances:Instances[].InstanceId}"
```

### Step 4: Verify lifecycle hook

```bash
aws autoscaling describe-lifecycle-hooks \
  --auto-scaling-group-name $ASG_NAME \
  --query "LifecycleHooks[0].{Name:LifecycleHookName,Transition:LifecycleTransition,Timeout:HeartbeatTimeout,DefaultResult:DefaultResult}"
```

### Step 5: Activate Cost Explorer tags

1. Open **AWS Cost Explorer** → **Cost allocation tags**
2. Activate: `Project`, `Environment`, `Owner`
3. Tags appear in Cost Explorer within 24 hours
4. Create a **Cost Category** grouping by `Project` tag to see per-project spend

---

## Real-World Use Cases

### 1. Internal tooling (dev/staging)
A CI/CD pipeline dashboard runs on a single instance. Engineers only use it
9:00–18:00 on weekdays. Scheduled scale-to-zero saves:
- Before: `t3.large` 24/7 → $64.75/mo
- After: `t3.large` 9h/day, Mon–Fri → $14.27/mo → **78% reduction**

### 2. E-commerce flash sales (Shopify pattern)
ASG sits behind an ALB. Scheduled scale-up fires 30 minutes before a known
promotion. Target tracking handles the unpredictable spike shape. Lifecycle hook
ensures no checkout request is dropped during post-sale scale-in.

### 3. Data pipeline workers (Airflow, EMR alternative)
Workers scale out when SQS queue depth rises (custom metric target tracking).
Lifecycle hook waits for the current Spark task to checkpoint before termination.
Workers scale back to zero when queue is empty — pay only for actual computation.

### 4. Multi-team cost attribution (startup scaling)
Every team's ASG gets `Owner=team-email` and `Project=service-name` tags.
AWS Cost Explorer shows per-team spend breakdown. FinOps team sets budget alerts
per `Project` tag — team leads receive emails before overspend happens.
