# Permanent migration history for live state addresses. Do not remove these
# blocks or reuse their legacy source addresses for new production resources.
moved {
  from = aws_db_instance.production
  to   = aws_db_instance.production[0]
}

moved {
  from = aws_lb.production
  to   = aws_lb.production[0]
}

moved {
  from = aws_lb_target_group.web
  to   = aws_lb_target_group.web[0]
}

moved {
  from = aws_lb_listener.http
  to   = aws_lb_listener.http[0]
}

moved {
  from = aws_lb_listener.https
  to   = aws_lb_listener.https[0]
}

moved {
  from = aws_iam_role_policy.events
  to   = aws_iam_role_policy.events[0]
}

moved {
  from = aws_cloudwatch_metric_alarm.alb_5xx
  to   = aws_cloudwatch_metric_alarm.alb_5xx[0]
}

moved {
  from = aws_cloudwatch_metric_alarm.unhealthy_targets
  to   = aws_cloudwatch_metric_alarm.unhealthy_targets[0]
}

moved {
  from = aws_cloudwatch_metric_alarm.database_storage
  to   = aws_cloudwatch_metric_alarm.database_storage[0]
}

moved {
  from = aws_cloudwatch_metric_alarm.database_cpu
  to   = aws_cloudwatch_metric_alarm.database_cpu[0]
}

moved {
  from = aws_cloudwatch_metric_alarm.database_connections
  to   = aws_cloudwatch_metric_alarm.database_connections[0]
}
