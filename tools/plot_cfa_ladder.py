import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Read the CSV
df = pd.read_csv('Band1all_CFA.csv')

# Extract LED sequence data
led1_flux = df['SRCS_LEDB1_FLUX'].fillna(0).values
led2_flux = df['SRCS_LEDB2_FLUX'].fillna(0).values

# Create mappings from flux to level number
led1_values = sorted(df['SRCS_LEDB1_FLUX'].dropna().unique())
led2_values = sorted(df['SRCS_LEDB2_FLUX'].dropna().unique())
led1_to_level = {float(v): i+1 for i, v in enumerate(led1_values)}
led2_to_level = {float(v): i+1 for i, v in enumerate(led2_values)}

# Remove rows with no LED activity
active_rows = (led1_flux > 0) | (led2_flux > 0)
led1_flux_active = led1_flux[active_rows]
led2_flux_active = led2_flux[active_rows]

# Get the level pairs for active rows
led1_levels = [led1_to_level.get(float(v), 0) if v > 0 else 0 for v in led1_flux_active]
led2_levels = [led2_to_level.get(float(v), 0) if v > 0 else 0 for v in led2_flux_active]

# Calculate total flux
total_flux = led1_flux_active + led2_flux_active

# Create figure
fig, ax = plt.subplots(figsize=(14, 6))

x = np.arange(len(led1_flux_active))
width = 0.6

# For log scale stacked bars, we need to plot LED16 from 0 to LED16,
# and LED26 from LED16 to LED16+LED26
bars1 = ax.bar(x, led1_flux_active, width, label='LED 1', color='steelblue', alpha=0.8, edgecolor='darkblue', linewidth=1.5)
bars2 = ax.bar(x, led2_flux_active, width, bottom=led1_flux_active, label='LED 2', color='salmon', alpha=0.8, edgecolor='darkred', linewidth=1.5)

# Set log scale
ax.set_yscale('log')

# Set y-axis limits to start at minimum value
min_flux = total_flux.min()
max_flux = total_flux.max()
ax.set_ylim(min_flux * 0.8, max_flux * 1.5)

# Customize plot
ax.set_xlabel('State Number', fontsize=12)
ax.set_ylabel(r'Total Flux (e$^-$ pix$^{-1}$ s$^{-1}$)', fontsize=12)
# ax.set_title('CFA Ladder: Total LED Flux by State', fontsize=14, pad=20)

# Set x-axis ticks and labels with level pairing
tick_positions = range(0, len(led1_flux_active), max(1, len(led1_flux_active)//20))
tick_labels = [f'{i}\n({led1_levels[i]},{led2_levels[i]})' for i in tick_positions]
ax.set_xticks(tick_positions)
ax.set_xticklabels(tick_labels, fontsize=9)

ax.grid(axis='y', alpha=0.3, linestyle='--', which='both')
ax.legend(fontsize=11, loc='upper left', framealpha=0.95)

# Add flux labels inside bars vertically, positioned at constant log distance from top
log_distance_from_top = 0.05  # log10 units: higher = further from top (e.g., 0.1, 0.15, 0.2)
factor = 10 ** log_distance_from_top  # Convert log distance to linear factor

# Helper function to format numbers: decimals if < 6, integer otherwise
def format_flux(value):
    return f'{value:.1f}' if value < 6 else f'{value:.0f}'

min_ratio = 1.3  # Minimum ratio to display label on log scale

for i, (led1, led2) in enumerate(zip(led1_flux_active, led2_flux_active)):
    total = led1 + led2

    # LED1 label (bottom segment) - at constant log distance from top
    # Only display if segment is large enough (not overlapping with LED2)
    if led1 > 0 and (led2 == 0 or led1 / led2 >= min_ratio):
        y_pos_led1 = np.sqrt(led1)#led1 / factor
        ax.text(i, y_pos_led1, format_flux(led1), ha='center', va='center',
                fontsize=7, fontweight='bold', color='white', rotation=90)

    # LED2 label (top segment) - at geometric mean between led1 and total (log space midpoint)
    # Only display if segment is large enough (not overlapping with LED1)
    if led2 > 0 and (led1 == 0 or total / led1 >= min_ratio):
        y_pos_led2 = np.sqrt(led1 * total)
        ax.text(i, y_pos_led2, format_flux(led2), ha='center', va='center',
                fontsize=7, fontweight='bold', color='white', rotation=90)

    # Total label on top
    if total > 0:
        total_str = format_flux(total)
        fontsize = 6 if len(total_str) == 4 else 8
        ax.text(i, total * 1.1, total_str, ha='center', va='bottom', fontsize=fontsize)

plt.subplots_adjust(left=0.06, right=0.99, top=0.95, bottom=0.15)
plt.savefig('CFA_ladder_stacked.png', dpi=150)
print("✓ Saved CFA_ladder_stacked.png")
