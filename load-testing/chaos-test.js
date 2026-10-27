// ResilienceLab — Continuous Traffic Generator for Chaos Testing
// Purpose: Simulates steady user traffic during fault injection experiments.
// Run: k6 run load-testing/chaos-test.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// Custom Metrics
const successfulRequests = new Counter('chaos_successful_requests');
const failedRequests = new Counter('chaos_failed_requests');
const availabilityRate = new Rate('chaos_availability_rate');
const orderDuration = new Trend('chaos_order_duration_ms');

export const options = {
  stages: [
    { duration: '10s', target: 5 },   // Warm-up to 5 VUs
    { duration: '75s', target: 8 },   // Sustained load during chaos injection
    { duration: '15s', target: 0 },   // Cool-down
  ],
  thresholds: {
    // Chaos experiments may experience transient latency, but overall availability target is >= 95%
    chaos_availability_rate: ['rate>=0.95'],
  },
};

export default function () {
  // 1. Health check (Liveness)
  const healthRes = http.get(`${BASE_URL}/health`, { timeout: '3s' });
  const healthOk = check(healthRes, {
    'liveness status 200': (r) => r.status === 200,
  });

  // 2. Readiness check (Database dependency)
  const readyRes = http.get(`${BASE_URL}/ready`, { timeout: '3s' });
  const readyOk = check(readyRes, {
    'readiness status 200': (r) => r.status === 200,
  });

  // 3. Create an order (Write path)
  const payload = JSON.stringify({
    customer_name: `chaos-client-${__VU}-${Date.now()}`,
  });
  const headers = { 'Content-Type': 'application/json' };

  const start = Date.now();
  const createRes = http.post(`${BASE_URL}/api/orders`, payload, { headers, timeout: '5s' });
  const duration = Date.now() - start;
  orderDuration.add(duration);

  const createOk = check(createRes, {
    'create order 201': (r) => r.status === 201,
  });

  if (healthOk && (createOk || readyOk)) {
    successfulRequests.add(1);
    availabilityRate.add(1);
  } else {
    failedRequests.add(1);
    availabilityRate.add(0);
  }

  sleep(0.5);
}
