-- ResilienceLab Database Initialization
-- This script runs on first PostgreSQL startup

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    status VARCHAR(30) DEFAULT 'pending' NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Create index on status for worker polling
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- Seed some test data
INSERT INTO orders (customer_name, status) VALUES
    ('Alice Johnson', 'pending'),
    ('Bob Smith', 'pending'),
    ('Carol Williams', 'completed');
