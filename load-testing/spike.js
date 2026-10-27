// ResilienceLab — k6 Spike Test
// Purpose: Test system behavior under sudden traffic spikes.
// Run: k6 run load-testing/spike.js

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '10s', target: 5 },    // Normal traffic
    { duration: '5s',  target: 30 },   // Sudden spike
    { duration: '30s', target: 30 },   // Sustain spike
    { duration: '10s', target: 5 },    // Return to normal
    { duration: '30s', target: 5 },    // Observe recovery
    { duration: '10s', target: 0 },    // Ramp down
  ],
};

export default function () {
  const res = http.get(`${BASE_URL}/api/orders`);
  check(res, {
    'status 200': (r) => r.status === 200,
  });

  sleep(0.3);
}
