SELECT contact_id, customer_id, campaign_month, arm, exposed, channel, offer,
       CAST(assigned_at AS TIMESTAMP) AS assigned_at, CAST(available_at AS TIMESTAMP) AS available_at
FROM bronze.crm_retention_contacts
