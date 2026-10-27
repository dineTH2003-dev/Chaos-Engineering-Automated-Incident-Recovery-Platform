// ResilienceLab — k6 Stress Test
// Purpose: Find the system's breaking point under increasing load.
// Run: k6 run load-testing/stress.js

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // Warm up
    { duration: '1m',  target: 25 },   // Moderate load
    { duration: '1m',  target: 50 },   // Heavy load
    { duration: '30s', target: 0 },    // Ramp down
  ],

  // No thresholds — we want to observe the breaking point.
};

export default function () {
  const ordersRes = http.get(`${BASE_URL}/api/orders`);
  check(ordersRes, {
    'status 200': (r) => r.status === 200,
  });

  const createRes = http.post(
    `${BASE_URL}/api/orders`,
    JSON.stringify({ customer_name: `stress-user-${__VU}` }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  check(createRes, {
    'created': (r) => r.status === 201 || r.status === 200,
  });

  sleep(0.5);
}
