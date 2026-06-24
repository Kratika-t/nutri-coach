import os
import sys
from mcp.server.fastmcp import FastMCP

# Initialize the FastMCP server
mcp = FastMCP("NutriCoachServer")

@mcp.tool()
def calculate_bmi(weight_kg: float, height_m: float) -> str:
    """Calculate the Body Mass Index (BMI) and determine weight category.
    
    Args:
        weight_kg: The weight of the person in kilograms (e.g. 70.0).
        height_m: The height of the person in meters (e.g. 1.75).
    """
    if height_m <= 0:
        return "Error: Height must be greater than zero."
    if weight_kg <= 0:
        return "Error: Weight must be greater than zero."
        
    bmi = weight_kg / (height_m * height_m)
    if bmi < 18.5:
        category = "Underweight"
    elif bmi < 25.0:
        category = "Normal weight"
    elif bmi < 30.0:
        category = "Overweight"
    else:
        category = "Obese"
        
    return f"BMI is {bmi:.1f}. Category: {category}."

@mcp.tool()
def get_food_nutrition(food_name: str) -> str:
    """Search the nutrition database to get calorie and macronutrient info for a food item.
    
    Args:
        food_name: The name of the food item to look up (e.g., 'apple', 'chicken breast').
    """
    database = {
        "apple": "52 kcal, 14g Carbs, 0.3g Protein, 0.2g Fat per 100g",
        "chicken breast": "165 kcal, 0g Carbs, 31g Protein, 3.6g Fat per 100g",
        "white rice": "130 kcal, 28g Carbs, 2.7g Protein, 0.3g Fat per 100g",
        "egg": "155 kcal, 1.1g Carbs, 13g Protein, 11g Fat per 100g",
        "salmon": "208 kcal, 0g Carbs, 20g Protein, 13g Fat per 100g",
        "avocado": "160 kcal, 8.5g Carbs, 2g Protein, 15g Fat per 100g",
        "broccoli": "34 kcal, 7g Carbs, 2.8g Protein, 0.4g Fat per 100g",
        "oats": "389 kcal, 66g Carbs, 16.9g Protein, 6.9g Fat per 100g",
    }
    
    food_lower = food_name.lower().strip()
    for item, nutrition in database.items():
        if item in food_lower:
            return f"Nutritional details for '{item}': {nutrition}."
            
    return f"Food item '{food_name}' was not found in the database. Please provide nutritional estimates manually."

@mcp.tool()
def log_meal(meal_name: str, total_calories: int) -> str:
    """Log a consumed meal with its calories to keep track of daily nutrition intake.
    
    Args:
        meal_name: The name of the meal or food item consumed.
        total_calories: The total calorie count for the meal.
    """
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "daily_intake.txt")
    
    try:
        with open(log_path, "a") as f:
            f.write(f"Meal: {meal_name} | Calories: {total_calories} kcal\n")
        return f"Successfully logged meal: '{meal_name}' ({total_calories} kcal)."
    except Exception as e:
        return f"Failed to log meal due to error: {str(e)}"

if __name__ == "__main__":
    mcp.run()
