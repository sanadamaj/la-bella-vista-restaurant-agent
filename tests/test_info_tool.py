import unittest

from tools.info_tool import GetRestaurantInfoInput, get_restaurant_info


class TestInfoTool(unittest.TestCase):
    def test_finds_vegan_menu_items(self):
        result = get_restaurant_info(GetRestaurantInfoInput(query="vegan"))
        self.assertTrue(result.found_any)
        self.assertTrue(any("vegan" in (i.get("dietary") or []) for i in result.menu_matches))

    def test_finds_parking_faq(self):
        result = get_restaurant_info(GetRestaurantInfoInput(query="parking"))
        self.assertTrue(result.found_any)
        topics = [f["topic"] for f in result.faq_matches]
        self.assertIn("parking", topics)

    def test_scope_menu_excludes_faqs(self):
        result = get_restaurant_info(GetRestaurantInfoInput(query="parking", scope="menu"))
        self.assertEqual(result.faq_matches, [])

    def test_no_match_returns_found_any_false_not_error(self):
        result = get_restaurant_info(GetRestaurantInfoInput(query="xyznonexistentdish"))
        self.assertFalse(result.found_any)
        self.assertEqual(result.match_count, 0)

    def test_empty_query_raises_value_error(self):
        with self.assertRaises(ValueError):
            get_restaurant_info(GetRestaurantInfoInput(query="   "))

    def test_allergen_search_finds_shellfish_dishes(self):
        result = get_restaurant_info(GetRestaurantInfoInput(query="shellfish"))
        self.assertTrue(result.found_any)
        self.assertTrue(all("shellfish" in i["allergens"] for i in result.menu_matches))


if __name__ == "__main__":
    unittest.main()
