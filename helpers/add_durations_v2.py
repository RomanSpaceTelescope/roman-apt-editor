import pandas as pd
import yaml

with open('ma_table_ref_revG.yaml', 'r') as f:
    data = yaml.safe_load(f)

# Build lookup: ma_table_name -> integration_duration list
science_tables = data.get('science_tables', {})
ma_int_durations = {}
for val in science_tables.values():
    name = val.get('ma_table_name')
    durations = val.get('integration_duration')
    if name and durations:
        ma_int_durations[name] = durations

def get_integration_duration(ma_table_name, resultants):
    durations = ma_int_durations.get(ma_table_name, [])
    idx = resultants - 1  # 1-indexed resultants -> 0-indexed list
    if 0 <= idx < len(durations):
        return durations[idx]
    elif durations:
        return durations[-1]
    return 0.0

df = pd.read_csv('APT_1024_LOLO_rev2.csv')

# Drop columns if they exist to recalculate
for col in ('ACTIVITY_DURATION_SEC', 'CUMULATIVE_TIME_SEC'):
    if col in df.columns:
        df = df.drop(col, axis=1)

OVERHEAD_PER_EXP = 12  # seconds

durations = []
for _, row in df.iterrows():
    exp_time = get_integration_duration(row['MA_TABLE'], int(row['RESULTANTS_PER_EXPOSURE']))
    activity_duration = int(row['NEXP']) * (exp_time + OVERHEAD_PER_EXP)
    durations.append(round(activity_duration, 3))

df['ACTIVITY_DURATION_SEC'] = durations
df['CUMULATIVE_TIME_SEC'] = df['ACTIVITY_DURATION_SEC'].cumsum().round(3)

df.to_csv('APT_1024_LOLO_rev2.csv', index=False)

total = df['CUMULATIVE_TIME_SEC'].iloc[-1]
print(f"Updated APT_1024_LOLO_rev2.csv — {len(df)} rows")
print(f"Total time: {total:.1f} s  ({total/3600:.2f} hr)")
print()
print(df[['MA_TABLE', 'NEXP', 'RESULTANTS_PER_EXPOSURE', 'ACTIVITY_DURATION_SEC', 'CUMULATIVE_TIME_SEC']].head(10).to_string(index=False))
