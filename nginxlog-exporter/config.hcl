listen {
  address = "127.0.0.1"
  port    = 4040
}

namespace "nginx" {
  source = {
    files = ["/var/log/nginx/monitoring.log"]
  }
  format = "$remote_addr - $remote_user [$time_local] \"$request\" $status $body_bytes_sent \"$http_referer\" \"$http_user_agent\" host=$host rt=$request_time"
  labels {
    app = "nginx"
  }
  # Only known vhosts become label values; scanners' random Host headers collapse to "other".
  relabel "vhost" {
    from      = "host"
    whitelist = ["sattle.axiosiiitl.dev", "battle.axiosiiitl.dev", "g1.axiosiiitl.dev", "g2.axiosiiitl.dev", "cdn.axiosiiitl.dev", "index.axiosiiitl.dev", "fetch.axiosiiitl.dev", "auth.axiosiiitl.dev", "stat.axiosiiitl.dev", "txn.axiosiiitl.dev", "m1.axiosiiitl.dev"]
  }
  histogram_buckets = [.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10]
}
