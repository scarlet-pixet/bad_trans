ALTER TABLE managed_operation_events ADD COLUMN location jsonb
    CHECK (
        location IS NULL OR (
            jsonb_typeof(location) = 'object'
            AND location ? 'kind'
            AND jsonb_typeof(location->'kind') = 'string'
            AND location ? 'countryCode'
            AND jsonb_typeof(location->'countryCode') = 'string'
            AND location->>'kind' IN ('POINT_OF_SALE','ATM_DEVICE','MERCHANT_REGISTRATION')
            AND location->>'countryCode' ~ '^[A-Z]{2}$'
        )
    );
