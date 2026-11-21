# API Server Memory Leak — Primary Analysis

## RSS growth profile

The api-server pods show monotonic RSS growth from 2GB to 14GB over a
6-hour window with flat traffic. Growth is linear at roughly 2GB/hour and
continues until the container hits its 16GB memory limit and is OOM-killed.
Restarting a pod resets RSS to the 2GB baseline, which rules out any
baseline footprint change. The growth started immediately after last
week's release that introduced the response caching layer.

## Heap dump analysis

A heap dump taken at 12GB RSS shows ~9.5GB retained by
`ResponseCacheEntry` objects. Each entry holds a fully serialized request
object (headers, body, session context) in addition to the cached
response. There are 3.1 million live entries; the cache has no TTL and no
eviction policy, so entries are never collected. Garbage collection itself
is healthy: GC pause times and frequency are normal, and the allocation
profile shows no unusual churn — the objects are simply reachable.

## Correlation with the response caching layer

The growth rate tracks `cache_entries` in the metrics almost exactly:
2GB/hour of RSS per ~500k new cache entries. Pods with the caching layer
flagged off (canary, 5% of traffic) stay flat at 2GB. Traffic volume is
unchanged week-over-week, and the request rate shows no correlation with
RSS once cache entries are controlled for. Conclusion: the leak is a
retention bug in the response caching layer, which retains request objects
indefinitely.