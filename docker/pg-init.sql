-- Create achat_observability database for Arize Phoenix (isolated from business DB)
SELECT 'CREATE DATABASE achat_observability'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'achat_observability')\gexec
