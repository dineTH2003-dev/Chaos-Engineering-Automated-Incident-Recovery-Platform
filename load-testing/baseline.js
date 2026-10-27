// ResilienceLab — k6 Baseline Load Test
// Purpose: Establish normal performance characteristics.
// Run: k6 run load-testing/baseline.js
//
// Thresholds define SLOs:
//   - <1% request failure rate
//   - p95 latency under 500ms

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 VUs
    { duration: '1m',  target: 10 },  // Hold at 10 VUs
    { duration: '30s', target: 0 },   // Ramp down
  ],

  thresholds: {
    http_req_failed:   ['rate<0.01'],   // <1% failure rate
    http_req_duration: ['p(95)<500'],   // p95 under 500ms
  },
};

export default function () {
  // Health check
  const healthRes = http.get(`${BASE_URL}/health`);
  check(healthRes, {
    'health: status 200': (r) => r.status === 200,
  });

  // List orders (DB-backed)
  const ordersRes = http.get(`${BASE_URL}/api/orders`);
  check(ordersRes, {
    'orders: status 200': (r) => r.status === 200,
    'orders: is array':   (r) => Array.isArray(JSON.parse(r.body)),
  });

  // Create an order (write path)
  const createRes = http.post(
    `${BASE_URL}/api/orders`,
    JSON.stringify({ customer_name: `k6-user-${__VU}-${__ITER}` }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  check(createRes, {
    'create: status 201': (r) => r.status === 201,
  });

  sleep(1);
}
