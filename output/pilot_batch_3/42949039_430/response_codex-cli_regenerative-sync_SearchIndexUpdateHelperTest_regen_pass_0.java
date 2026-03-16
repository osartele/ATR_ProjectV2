/*
* Copyright 2015 herd contributors
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
*     http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/
package org.finra.herd.service.helper;

import static org.finra.herd.model.dto.SearchIndexUpdateDto.MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE;
import static org.finra.herd.model.dto.SearchIndexUpdateDto.MESSAGE_TYPE_TAG_UPDATE;
import static org.finra.herd.model.dto.SearchIndexUpdateDto.SEARCH_INDEX_UPDATE_TYPE_UPDATE;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.MatcherAssert.assertThat;
import static org.junit.Assert.fail;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.List;

import org.junit.Before;
import org.junit.Test;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import org.finra.herd.core.helper.ConfigurationHelper;
import org.finra.herd.dao.helper.JsonHelper;
import org.finra.herd.model.dto.ConfigurationValue;
import org.finra.herd.model.dto.NotificationMessage;
import org.finra.herd.model.dto.SearchIndexUpdateDto;
import org.finra.herd.model.jpa.BusinessObjectDefinitionEntity;
import org.finra.herd.model.jpa.MessageTypeEntity;
import org.finra.herd.model.jpa.TagEntity;
import org.finra.herd.service.AbstractServiceTest;
import org.finra.herd.service.NotificationMessagePublishingService;

public class SearchIndexUpdateHelperTest extends AbstractServiceTest
{
    @Mock
    private ConfigurationHelper configurationHelper;

    @Mock
    private JsonHelper jsonHelper;

    @Mock
    private NotificationMessagePublishingService notificationMessagePublishingService;

    @InjectMocks
    private SearchIndexUpdateHelper searchIndexUpdateHelper;

    @Before
    public void before()
    {
        MockitoAnnotations.initMocks(this);
    }

    @Test
    public void testModifyBusinessObjectDefinitionInSearchIndex()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionInSearchIndex(businessObjectDefinitionEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verify(notificationMessagePublishingService)
            .addNotificationMessageToDatabaseQueue(new NotificationMessage(MessageTypeEntity.MessageEventTypes.SQS.name(), AWS_SQS_QUEUE_NAME, MESSAGE_TEXT, NO_MESSAGE_HEADERS));
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionInSearchIndexBlankMessage()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionInSearchIndex(businessObjectDefinitionEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionInSearchIndexNoSqsQueueName()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(BLANK_TEXT);

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyBusinessObjectDefinitionInSearchIndex(businessObjectDefinitionEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionsInSearchIndex()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);
        List<BusinessObjectDefinitionEntity> businessObjectDefinitionEntityList = new ArrayList<>();
        businessObjectDefinitionEntityList.add(businessObjectDefinitionEntity);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionsInSearchIndex(businessObjectDefinitionEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verify(notificationMessagePublishingService)
            .addNotificationMessageToDatabaseQueue(new NotificationMessage(MessageTypeEntity.MessageEventTypes.SQS.name(), AWS_SQS_QUEUE_NAME, MESSAGE_TEXT, NO_MESSAGE_HEADERS));
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionsInSearchIndexBlankMessage()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);
        List<BusinessObjectDefinitionEntity> businessObjectDefinitionEntityList = new ArrayList<>();
        businessObjectDefinitionEntityList.add(businessObjectDefinitionEntity);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyBusinessObjectDefinitionsInSearchIndex(businessObjectDefinitionEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyBusinessObjectDefinitionsInSearchIndexNoSqsQueueName()
    {
        // Create a business object data entity
        BusinessObjectDefinitionEntity businessObjectDefinitionEntity = new BusinessObjectDefinitionEntity();
        businessObjectDefinitionEntity.setId(1L);
        List<BusinessObjectDefinitionEntity> businessObjectDefinitionEntityList = new ArrayList<>();
        businessObjectDefinitionEntityList.add(businessObjectDefinitionEntity);

        // Create a list of business object definition ids
        List<Long> businessObjectDefinitionIds = new ArrayList<>();
        businessObjectDefinitionIds.add(businessObjectDefinitionEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto =
            new SearchIndexUpdateDto(MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE, businessObjectDefinitionIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn("");

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyBusinessObjectDefinitionsInSearchIndex(businessObjectDefinitionEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagInSearchIndex()
    {
        // Create a tag entity
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyTagInSearchIndex(tagEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verify(notificationMessagePublishingService)
            .addNotificationMessageToDatabaseQueue(new NotificationMessage(MessageTypeEntity.MessageEventTypes.SQS.name(), AWS_SQS_QUEUE_NAME, MESSAGE_TEXT, NO_MESSAGE_HEADERS));
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagInSearchIndexBlankMessage()
    {
        // Create a tag entity
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyTagInSearchIndex(tagEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagInSearchIndexEmptyQueueName()
    {
        // Create a tag entity
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn("");

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyTagInSearchIndex(tagEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagInSearchIndexSearchIndexUpdateJmsListenerDisabled()
    {
        // Create a tag entity
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("false");

        // Call the method under test
        searchIndexUpdateHelper.modifyTagInSearchIndex(tagEntity, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

@Test
public void testModifyTagsInSearchIndex() throws Exception
{
    org.finra.herd.service.helper.SearchIndexUpdateHelper helper = new org.finra.herd.service.helper.SearchIndexUpdateHelper();
    org.finra.herd.core.helper.JsonHelper jsonHelperMock = org.mockito.Mockito.mock(org.finra.herd.core.helper.JsonHelper.class);

    java.lang.reflect.Field jsonHelperField = org.finra.herd.service.helper.SearchIndexUpdateHelper.class.getDeclaredField("jsonHelper");
    jsonHelperField.setAccessible(true);
    jsonHelperField.set(helper, jsonHelperMock);

    final RuntimeException expected = new RuntimeException("stopAfterJsonCheck");
    final java.util.List<Long> expectedTagIds = java.util.Arrays.asList(1L, 2L);
    final String modificationType = "UPDATE";

    org.mockito.Mockito.when(jsonHelperMock.objectToJson(org.mockito.Matchers.any(org.finra.herd.model.dto.SearchIndexUpdateDto.class)))
        .thenAnswer(new org.mockito.stubbing.Answer<String>()
        {
            @Override
            public String answer(org.mockito.invocation.InvocationOnMock invocation) throws Throwable
            {
                Object dto = invocation.getArguments()[0];
                boolean messageTypeMatched = false;
                boolean modificationTypeMatched = false;
                boolean tagIdsMatched = false;

                for (java.lang.reflect.Field field : dto.getClass().getDeclaredFields())
                {
                    field.setAccessible(true);
                    Object value = field.get(dto);

                    if (value instanceof String &&
                        org.finra.herd.model.dto.SearchIndexUpdateDto.MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE.equals(value))
                    {
                        messageTypeMatched = true;
                    }
                    else if (value instanceof String && modificationType.equals(value))
                    {
                        modificationTypeMatched = true;
                    }
                    else if (value instanceof java.util.List && expectedTagIds.equals(value))
                    {
                        tagIdsMatched = true;
                    }
                }

                org.junit.Assert.assertTrue(messageTypeMatched);
                org.junit.Assert.assertTrue(modificationTypeMatched);
                org.junit.Assert.assertTrue(tagIdsMatched);

                throw expected;
            }
        });

    java.util.List<org.finra.herd.model.jpa.TagEntity> tagEntityList = new java.util.ArrayList<>();
    org.finra.herd.model.jpa.TagEntity tagEntity1 = new org.finra.herd.model.jpa.TagEntity();
    tagEntity1.setId(1L);
    tagEntityList.add(tagEntity1);

    org.finra.herd.model.jpa.TagEntity tagEntity2 = new org.finra.herd.model.jpa.TagEntity();
    tagEntity2.setId(2L);
    tagEntityList.add(tagEntity2);

    try
    {
        helper.modifyTagsInSearchIndex(tagEntityList, modificationType);
        org.junit.Assert.fail();
    }
    catch (RuntimeException e)
    {
        org.junit.Assert.assertSame(expected, e);
    }
}
    @Test
    public void testModifyTagsInSearchIndexBlankMessage()
    {
        // Create a tag entity list
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);
        List<TagEntity> tagEntityList = new ArrayList<>();
        tagEntityList.add(tagEntity);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn("");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn(AWS_SQS_QUEUE_NAME);

        // Call the method under test
        searchIndexUpdateHelper.modifyTagsInSearchIndex(tagEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verifyNoMoreInteractionsHelper();
    }

    @Test
    public void testModifyTagsInSearchIndexNoSqsQueueName()
    {
        // Create a tag entity list
        TagEntity tagEntity = new TagEntity();
        tagEntity.setId(1L);
        List<TagEntity> tagEntityList = new ArrayList<>();
        tagEntityList.add(tagEntity);

        // Create a list of tag ids
        List<Long> tagIds = new ArrayList<>();
        tagIds.add(tagEntity.getId());

        // Create a search index dto
        SearchIndexUpdateDto searchIndexUpdateDto = new SearchIndexUpdateDto(MESSAGE_TYPE_TAG_UPDATE, tagIds, SEARCH_INDEX_UPDATE_TYPE_UPDATE);

        // Mock the call to external methods
        when(jsonHelper.objectToJson(searchIndexUpdateDto)).thenReturn(MESSAGE_TEXT);
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED)).thenReturn("true");
        when(configurationHelper.getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME)).thenReturn("");

        // Call the method under test
        try
        {
            searchIndexUpdateHelper.modifyTagsInSearchIndex(tagEntityList, SEARCH_INDEX_UPDATE_TYPE_UPDATE);
            fail();
        }
        catch (IllegalStateException illegalStateException)
        {
            assertThat("Function is null.", illegalStateException.getMessage(),
                is("SQS queue name not found. Ensure the \"search.index.update.sqs.queue.name\" configuration entry is configured."));
        }

        // Verify the calls to external methods
        verify(jsonHelper).objectToJson(searchIndexUpdateDto);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_JMS_LISTENER_ENABLED);
        verify(configurationHelper).getProperty(ConfigurationValue.SEARCH_INDEX_UPDATE_SQS_QUEUE_NAME);
        verifyNoMoreInteractionsHelper();
    }

    /**
     * Checks if any of the mocks has any interaction.
     */
    private void verifyNoMoreInteractionsHelper()
    {
        verifyNoMoreInteractions(configurationHelper, jsonHelper, notificationMessagePublishingService);
    }
}
