import pandas as pd
import yaml

# Load the MA table reference
with open('ma_table_ref_revG.yaml', 'r') as f:
    ma_tables = yaml.safe_load(f)

def get_exposure_time(ma_table_name, resultants_per_exposure):
    """Get the integration/exposure time for a given MA table and resultants count."""

    # Search through all sections in the YAML to find the MA table
    for section_key in ma_tables:
        section = ma_tables[section_key]
        if isinstance(section, dict) and 'ma_table_name' in section:
            if section['ma_table_name'] == ma_table_name:
                # Found the MA table, look for integration_duration array
                if 'integration_duration' in section:
                    durations = section['integration_duration']
                    # The array is indexed by (resultants - 1)
                    # For example, 13 resultants means index 12
                    idx = resultants_per_exposure - 1
                    if 0 <= idx < len(durations):
                        return durations[idx]
                    else:
                        # If index is out of range, return the last available value
                        return durations[-1] if durations else 0

    return 0  # Default fallback if MA table not found

# Read the CSV
df = pd.read_csv('APT_1024_LOLO_rev2.csv')

# Calculate activity duration and cumulative time
durations = []
cumulative_time = 0

for idx, row in df.iterrows():
    ma_table = row['MA_TABLE']
    nexp = row['NEXP']
    resultants = row['RESULTANTS_PER_EXPOSURE']

    # Get exposure time per exposure
    exp_time = get_exposure_time(ma_table, resultants)

    # Calculate activity duration: (exposure_time * NEXP) + (12 * NEXP)
    activity_duration = (exp_time * nexp) + (12 * nexp)
    durations.append(activity_duration)

    # Accumulate cumulative time
    cumulative_time += activity_duration

# Add columns
df['ACTIVITY_DURATION_SEC'] = durations
df['CUMULATIVE_TIME_SEC'] = df['ACTIVITY_DURATION_SEC'].cumsum()

# Save the updated CSV
df.to_csv('APT_1024_LOLO_rev2.csv', index=False)

print(f"Updated APT_1024_LOLO_rev2.csv")
print(f"Total observations: {len(df)}")
print(f"Total cumulative time: {df['CUMULATIVE_TIME_SEC'].iloc[-1]:.2f} seconds ({df['CUMULATIVE_TIME_SEC'].iloc[-1]/3600:.2f} hours)")
print(f"\nFirst few rows with new columns:")
print(df[['MA_TABLE', 'NEXP', 'RESULTANTS_PER_EXPOSURE', 'ACTIVITY_DURATION_SEC', 'CUMULATIVE_TIME_SEC']].head(10))
