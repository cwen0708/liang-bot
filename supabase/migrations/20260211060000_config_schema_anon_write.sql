-- Allow anon to insert/update config_schema from frontend editor
CREATE POLICY "config_schema_anon_insert"
    ON config_schema FOR INSERT
    TO anon
    WITH CHECK (true);

CREATE POLICY "config_schema_anon_update"
    ON config_schema FOR UPDATE
    TO anon
    USING (true)
    WITH CHECK (true);
